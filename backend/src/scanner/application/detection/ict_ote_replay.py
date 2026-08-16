"""OTE and Premium/Discount replay service (Sprint S6)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneRepository,
    IctZoneTransitionRecord,
    IctZoneTransitionRepository,
)
from scanner.domain.common import Candle
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
    bracketed_dealing_range,
    evaluate_pd_context,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    detect_external_swings,
)
from scanner.shared import Timeframe

ICT_OTE_ALGO_VERSION = "s6-ote-v1"

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

        dealing_ranges = 0
        impulse_legs = 0
        otes_detected = 0
        zones_upserted = 0

        for index in range(len(candles)):
            atr = _atr_at(candles, index)

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
    ) -> int:
        current = _record_to_ote(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(start_index, len(candles)):
            if current.state in {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            }:
                break

            atr = _atr_at(candles, index)

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
                    candle_index,
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
    eligible = [swing for swing in swings if swing.index <= index]

    highs = [swing for swing in eligible if swing.kind is SwingKind.HIGH]

    lows = [swing for swing in eligible if swing.kind is SwingKind.LOW]

    if not highs or not lows:
        return None

    high = max(
        highs,
        key=lambda swing: swing.index,
    )

    low = max(
        lows,
        key=lambda swing: swing.index,
    )

    range_id = _range_id(low, high)

    return bracketed_dealing_range(
        range_id=range_id,
        external_low=low.price,
        external_high=high.price,
        low_anchor_index=low.index,
        high_anchor_index=high.index,
        close=candles[index].close,
    )


def _impulse_leg_at(
    swings: tuple[SwingPoint, ...],
    candles: list[Candle],
    index: int,
) -> ImpulseLeg | None:
    eligible = sorted(
        (swing for swing in swings if swing.index <= index),
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
        created_index=record.created_index,
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
    origin = candles[ote.created_index].close

    if ote.polarity is ZonePolarity.BULLISH:
        return current >= ote.origin_price or current >= origin

    return current <= ote.origin_price or current <= origin


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
    raw = f"pd|{low.index}|{low.price}|{high.index}|{high.price}"

    return hashlib.sha256(raw.encode()).hexdigest()


def _leg_id(
    origin: SwingPoint,
    extreme: SwingPoint,
) -> str:
    raw = f"impulse|{origin.index}|{origin.price}|{extreme.index}|{extreme.price}"

    return hashlib.sha256(raw.encode()).hexdigest()


def _transition_id(
    zone_id: str,
    from_state: str,
    to_state: str,
    candle_index: int,
    transitioned_at: datetime,
) -> str:
    raw = f"{zone_id}|{from_state}|{to_state}|{candle_index}|{transitioned_at.isoformat()}"

    return hashlib.sha256(raw.encode()).hexdigest()


def _atr_at(
    candles: list[Candle],
    index: int,
) -> Decimal:
    if index <= 0:
        return _ZERO

    start = max(
        1,
        index - _ATR_PERIOD + 1,
    )

    true_ranges: list[Decimal] = []

    for current_index in range(
        start,
        index + 1,
    ):
        candle = candles[current_index]
        previous_close = candles[current_index - 1].close

        true_range = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )

        true_ranges.append(true_range)

    if not true_ranges:
        return _ZERO

    return sum(
        true_ranges,
        _ZERO,
    ) / Decimal(len(true_ranges))
