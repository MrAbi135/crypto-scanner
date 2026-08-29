"""OTE and Premium/Discount replay service (Sprint S6)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.detection.window_time import rebased_indices
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneRepository,
    IctZoneTransitionRecord,
    IctZoneTransitionRepository,
)
from scanner.domain.common import Candle, wilder_atr_series
from scanner.domain.ict.model import ZoneBand, ZonePolarity, ZoneState
from scanner.domain.ict.ote import (
    ImpulseDirection,
    ImpulseLeg,
    OptimalTradeEntry,
    advance_ote,
    detect_ote,
)
from scanner.domain.ict.pd import (
    DealingRange,
    PdContext,
    dealing_range_at,
    evaluate_pd_context,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    detect_external_swings,
    swing_window,
)
from scanner.shared import Timeframe

# v3: lifecycle indices rebased into today's window by created_at (see
# window_time.py) -- §5.8's 100-candle expiry was unreachable for
# tail-frozen OTEs -- and the origin-candle read guards against a rebased
# negative index wrapping to the window's far end.
ICT_OTE_ALGO_VERSION = "s6-ote-v3"

_ATR_PERIOD = 14
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class IctOteReplayReport:
    symbol: str
    timeframe: Timeframe
    dealing_ranges: int
    impulse_legs: int
    otes_detected: int
    zones_upserted: int
    transitions: int
    live_otes: int


class IctOteReplayService:
    """Replay deterministic external-range PD context and OTE zones."""

    def __init__(
        self,
        candles: CandleRepository,
        zones: IctZoneRepository,
        transitions: IctZoneTransitionRepository,
        clock: Clock,
        *,
        algo_version: str = ICT_OTE_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._zones = zones
        self._transitions = transitions
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> IctOteReplayReport:
        if end <= start:
            raise ValueError("end must be greater than start")

        candles = list(
            await self._candles.fetch_series(
                symbol,
                timeframe,
                start,
                end,
            )
        )

        if not candles:
            return IctOteReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                dealing_ranges=0,
                impulse_legs=0,
                otes_detected=0,
                zones_upserted=0,
                transitions=0,
                live_otes=0,
            )

        external_swings = detect_external_swings(candles)

        # Once per run, not once per candle -- see `_atr_at`.
        atrs = wilder_atr_series(candles)

        dealing_ranges = 0
        impulse_legs = 0
        otes_detected = 0
        zones_upserted = 0

        for index in range(len(candles)):
            atr = _atr_at(atrs, index)

            if atr <= _ZERO:
                continue

            dealing_range = _dealing_range_at(
                external_swings,
                candles,
                index,
            )

            if dealing_range is None:
                continue

            dealing_ranges += 1

            leg = _impulse_leg_at(
                external_swings,
                candles,
                index,
            )

            if leg is None:
                continue

            impulse_legs += 1

            ote = detect_ote(
                leg,
                atr=atr,
            )

            if ote is None:
                continue

            otes_detected += 1

            await self._zones.upsert(
                _ote_record(
                    symbol=symbol,
                    timeframe=timeframe,
                    ote=ote,
                    dealing_range=dealing_range,
                    updated_at=self._clock.now(),
                    algo_version=self._algo_version,
                )
            )

            zones_upserted += 1

        transition_count = 0

        live = await self._zones.list_live(
            symbol,
            timeframe,
        )

        for record in live:
            if record.zone_type != "OTE":
                continue

            transition_count += await self._replay_ote_lifecycle(
                record,
                candles,
                external_swings,
                atrs,
            )

        live_after = await self._zones.list_live(
            symbol,
            timeframe,
        )

        live_otes = sum(1 for record in live_after if record.zone_type == "OTE")

        return IctOteReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            dealing_ranges=dealing_ranges,
            impulse_legs=impulse_legs,
            otes_detected=otes_detected,
            zones_upserted=zones_upserted,
            transitions=transition_count,
            live_otes=live_otes,
        )

    async def _replay_ote_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
        external_swings: tuple[SwingPoint, ...],
        atrs: Sequence[Decimal | None],
    ) -> int:
        created, confirmed = rebased_indices(record, candles, record.timeframe)
        current = _record_to_ote(record, created_index=created)

        # Rebased by created_at -- see window_time.py.
        start_index = max(confirmed + 1, 0)

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(start_index, len(candles)):
            if current.state in {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            }:
                break

            atr = _atr_at(atrs, index)

            if atr <= _ZERO:
                continue

            dealing_range = _dealing_range_at(
                external_swings,
                candles,
                index,
            )

            if dealing_range is None:
                continue

            pd_context = evaluate_pd_context(
                dealing_range,
                close=candles[index].close,
                atr=atr,
            )

            trend_matches = _trend_matches_ote(
                current,
                candles,
                index,
            )

            leg_end_consumed = _leg_end_consumed(
                current,
                candles[index],
            )

            previous_state = current.state

            updated = advance_ote(
                current,
                candles[index],
                candle_index=index,
                pd_context=pd_context,
                trend_matches=trend_matches,
                leg_end_consumed=leg_end_consumed,
            )

            if updated.state is previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=previous_state.value,
                to_state=updated.state.value,
                reason=_ote_transition_reason(updated.state),
                candle_index=index,
                transitioned_at=candles[index].close_time,
                pd_context=pd_context,
            )

            if not changed:
                break

            transitions += 1

            record = IctZoneRecord(
                zone_id=record.zone_id,
                symbol=record.symbol,
                timeframe=record.timeframe,
                zone_type=record.zone_type,
                polarity=record.polarity,
                state=updated.state.value,
                grade=record.grade,
                band_low=record.band_low,
                band_high=record.band_high,
                refined_low=record.refined_low,
                refined_high=record.refined_high,
                created_index=record.created_index,
                confirmed_index=record.confirmed_index,
                created_at=record.created_at,
                updated_at=self._clock.now(),
                parent_zone_id=record.parent_zone_id,
                dealing_range_id=record.dealing_range_id,
                stale_context=record.stale_context,
                gap_adjacent=record.gap_adjacent,
                origin_swept=record.origin_swept,
                evidence=record.evidence,
            )

            current = updated

        return transitions

    async def _transition_zone(
        self,
        *,
        record: IctZoneRecord,
        from_state: str,
        to_state: str,
        reason: str,
        candle_index: int,
        transitioned_at: datetime,
        pd_context: PdContext,
    ) -> bool:
        changed = await self._zones.transition(
            record.zone_id,
            from_state=from_state,
            to_state=to_state,
            updated_at=self._clock.now(),
        )

        if not changed:
            return False

        evidence = json.dumps(
            {
                "algo_version": self._algo_version,
                "zone_id": record.zone_id,
                "zone_type": "OTE",
                "from_state": from_state,
                "to_state": to_state,
                "pd_state": pd_context.state.value,
                "range_id": pd_context.range_id,
                "range_position": (
                    str(pd_context.range_position)
                    if pd_context.range_position is not None
                    else None
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        await self._transitions.append(
            IctZoneTransitionRecord(
                transition_id=_transition_id(
                    record.zone_id,
                    from_state,
                    to_state,
                    transitioned_at,
                ),
                zone_id=record.zone_id,
                symbol=record.symbol,
                timeframe=record.timeframe,
                zone_type="OTE",
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                transitioned_at=transitioned_at,
                candle_index=candle_index,
                evidence=evidence,
            )
        )

        return True


def _dealing_range_at(
    swings: tuple[SwingPoint, ...],
    candles: list[Candle],
    index: int,
) -> DealingRange | None:
    """§5.7's range selection, which now lives in the domain beside the rule.

    It was private here, so §8's PD gate had no way to ask the same question
    and G3 stayed a hardcoded pass. The `range_id` shape is preserved because
    it is already stored on every OTE zone.
    """
    # Same confirmation-aware cut as pd.dealing_range_at now enforces at the
    # root; kept here only to size the slice handed on.
    eligible = [swing for swing in swings if swing.index + swing_window(swing.strength) <= index]

    highs = [swing for swing in eligible if swing.kind is SwingKind.HIGH]
    lows = [swing for swing in eligible if swing.kind is SwingKind.LOW]

    if not highs or not lows:
        return None

    high = max(highs, key=lambda swing: swing.index)
    low = max(lows, key=lambda swing: swing.index)

    return dealing_range_at(
        swings,
        close=candles[index].close,
        index=index,
        range_id=_range_id(low, high),
    )


def _impulse_leg_at(
    swings: tuple[SwingPoint, ...],
    candles: list[Candle],
    index: int,
) -> ImpulseLeg | None:
    eligible = sorted(
        (swing for swing in swings if swing.index + swing_window(swing.strength) <= index),
        key=lambda swing: swing.index,
    )

    if len(eligible) < 2:
        return None

    origin = eligible[-2]
    extreme = eligible[-1]

    if origin.kind is extreme.kind:
        return None

    if origin.kind is SwingKind.LOW and extreme.kind is SwingKind.HIGH:
        direction = ImpulseDirection.BULLISH
    elif origin.kind is SwingKind.HIGH and extreme.kind is SwingKind.LOW:
        direction = ImpulseDirection.BEARISH
    else:
        return None

    return ImpulseLeg(
        leg_id=_leg_id(origin, extreme),
        direction=direction,
        origin_price=origin.price,
        extreme_price=extreme.price,
        origin_index=origin.index,
        end_index=extreme.index,
        confirmed_at=candles[extreme.index].close_time,
    )


def _ote_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    ote: OptimalTradeEntry,
    dealing_range: DealingRange,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": algo_version,
            "detector": "OTE",
            "leg_id": ote.leg_id,
            "origin_price": str(ote.origin_price),
            "extreme_price": str(ote.extreme_price),
            "range_id": dealing_range.range_id,
            "range_low": str(dealing_range.low),
            "range_high": str(dealing_range.high),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=ote.ote_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="OTE",
        polarity=ote.polarity.value,
        state=ote.state.value,
        grade="OTE",
        band_low=ote.band.low,
        band_high=ote.band.high,
        refined_low=None,
        refined_high=None,
        created_index=ote.created_index,
        confirmed_index=ote.created_index,
        created_at=ote.created_at,
        updated_at=updated_at,
        parent_zone_id=None,
        dealing_range_id=dealing_range.range_id,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=None,
        evidence=evidence,
    )


def _record_to_ote(
    record: IctZoneRecord,
    *,
    created_index: int | None = None,
) -> OptimalTradeEntry:
    if record.zone_type != "OTE":
        raise ValueError("record is not an OTE")

    evidence: dict[str, object] = json.loads(record.evidence)

    leg_id = evidence.get("leg_id")
    origin = evidence.get("origin_price")
    extreme = evidence.get("extreme_price")

    if not isinstance(leg_id, str):
        raise ValueError("OTE evidence missing leg_id")

    if not isinstance(origin, str):
        raise ValueError("OTE evidence missing origin_price")

    if not isinstance(extreme, str):
        raise ValueError("OTE evidence missing extreme_price")

    return OptimalTradeEntry(
        ote_id=record.zone_id,
        leg_id=leg_id,
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        origin_price=Decimal(origin),
        extreme_price=Decimal(extreme),
        created_index=(record.created_index if created_index is None else created_index),
        created_at=record.created_at,
        state=ZoneState(record.state),
    )


def _trend_matches_ote(
    ote: OptimalTradeEntry,
    candles: list[Candle],
    index: int,
) -> bool:
    if index <= ote.created_index:
        return True

    current = candles[index].close

    # The stored origin_price is the durable half of this test. The origin
    # CANDLE's close is only readable while that candle is still in the
    # window -- a rebased created_index is negative once the zone predates
    # it, and Python's negative indexing would silently hand back a candle
    # from the WRONG END of the window.
    in_window = 0 <= ote.created_index < len(candles)
    origin = candles[ote.created_index].close if in_window else None

    if ote.polarity is ZonePolarity.BULLISH:
        return current >= ote.origin_price or (origin is not None and current >= origin)

    return current <= ote.origin_price or (origin is not None and current <= origin)


def _leg_end_consumed(
    ote: OptimalTradeEntry,
    candle: Candle,
) -> bool:
    if ote.polarity is ZonePolarity.BULLISH:
        return candle.close < ote.origin_price

    return candle.close > ote.origin_price


def _ote_transition_reason(
    state: ZoneState,
) -> str:
    reasons = {
        ZoneState.TESTED: "ote_test",
        ZoneState.MITIGATED: "ote_mitigation",
        ZoneState.INVALIDATED: "context_invalidation",
        ZoneState.EXPIRED: "zone_max_age",
    }

    return reasons[state]


def _range_id(
    low: SwingPoint,
    high: SwingPoint,
) -> str:
    """A dealing range, identified by the swings that bound it.

    `SwingPoint.index` was here and is gone: it is an offset inside the window
    that detected the swing, so the same range was a new range on every pass —
    and every OTE zone hanging off it was a new zone. `open_time` is the same
    swing named durably.
    """
    return _sha(
        f"pd|{low.open_time.isoformat()}|{low.price}|{high.open_time.isoformat()}|{high.price}"
    )


def _leg_id(
    origin: SwingPoint,
    extreme: SwingPoint,
) -> str:
    """An impulse leg, identified by its two swings. See `_range_id`."""

    return _sha(
        f"impulse|{origin.open_time.isoformat()}|{origin.price}"
        f"|{extreme.open_time.isoformat()}|{extreme.price}"
    )


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _transition_id(
    zone_id: str,
    from_state: str,
    to_state: str,
    transitioned_at: datetime,
) -> str:
    """See `ict_replay._build_transition_id` — the window-local index is gone."""

    raw = f"{zone_id}|{from_state}|{to_state}|{transitioned_at.isoformat()}"

    return hashlib.sha256(raw.encode()).hexdigest()


def _atr_at(
    atrs: Sequence[Decimal | None],
    index: int,
) -> Decimal:
    """Wilder ATR at `index`, with the seeding window reported as zero.

    Reads a series computed once per run. `wilder_atr` re-seeds the recurrence
    from candle zero on every call, so calling it per candle made each replay
    quadratic -- together the services spent about 99 seconds of CPU per pass
    inside `true_range`, against a budget of 104 seconds for the whole pass.

    Zero for the seeding window, as before: every call site here guards with
    ``if atr <= 0``, and §1.9's warm-up gate keeps production out of it.
    """

    if index < 0 or index >= len(atrs):
        return Decimal("0")

    return atrs[index] or Decimal("0")
