"""Uniform ICT zone interaction replay (SLS §5.9)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import CandleRepository
from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionContextRepository,
    IctZoneInteractionRecord,
    IctZoneInteractionRepository,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.domain.common import Candle, wilder_atr_series
from scanner.domain.ict import (
    TERMINAL_ZONE_STATES,
    InteractionKind,
    ZoneBand,
    ZonePolarity,
    evaluate_zone_interaction,
)
from scanner.domain.structure import (
    BreakDirection,
    SwingKind,
    SwingPoint,
    SwingStrength,
    detect_bos,
    detect_internal_swings,
    swing_window,
)
from scanner.shared import Timeframe

ICT_INTERACTION_ALGO_VERSION = "s6-interaction-v3"

_ATR_PERIOD = 14
_CONFIRMATION_MAX_LTF_CANDLES = 5
_ZERO = Decimal("0")

# One definition, in the domain beside the state machines that produce them.
_TERMINAL_STATES = TERMINAL_ZONE_STATES


@dataclass(frozen=True, slots=True)
class IctInteractionReplayReport:
    symbol: str
    timeframe: Timeframe
    zones_evaluated: int
    touches: int
    rejections: int
    mitigations: int
    respects: int
    violations: int
    confirmations: int
    interactions_inserted: int


class IctZoneInteractionReplayService:
    """Apply one uniform interaction grammar to every S6 zone type."""

    def __init__(
        self,
        candles: CandleRepository,
        context: IctZoneInteractionContextRepository,
        interactions: IctZoneInteractionRepository,
        *,
        algo_version: str = ICT_INTERACTION_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._context = context
        self._interactions = interactions
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> IctInteractionReplayReport:
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
            return _empty_report(
                symbol,
                timeframe,
            )

        # Once per run, not once per candle per zone -- see `_atr_at`.
        atrs = wilder_atr_series(candles)

        zones = await self._context.list_zones(
            symbol,
            timeframe,
        )

        lower_timeframe = _lower_timeframe(timeframe)

        lower_candles: list[Candle] = []
        lower_internal_swings: tuple[SwingPoint, ...] = ()

        if lower_timeframe is not None:
            lower_candles = list(
                await self._candles.fetch_series(
                    symbol,
                    lower_timeframe,
                    start,
                    end,
                )
            )

            if lower_candles:
                lower_internal_swings = detect_internal_swings(lower_candles)

        counts = {
            InteractionKind.TOUCH: 0,
            InteractionKind.REJECTION: 0,
            InteractionKind.MITIGATION: 0,
            InteractionKind.RESPECT: 0,
            InteractionKind.VIOLATION: 0,
            InteractionKind.CONFIRMATION: 0,
        }

        inserted_total = 0
        zones_evaluated = 0

        # One query for every zone's transitions rather than one per zone: the
        # loop below ran 630 round trips before evaluating a single candle.
        transitions_by_zone = await self._context.list_transitions_for(
            [record.zone_id for record in zones]
        )

        for record in zones:
            zones_evaluated += 1

            transitions = transitions_by_zone.get(record.zone_id, ())

            terminal_index = _terminal_index(transitions)

            start_index = max(
                0,
                record.confirmed_index + 1,
            )

            if start_index >= len(candles):
                continue

            stop_index = len(candles) - 1

            if terminal_index is not None:
                stop_index = min(
                    stop_index,
                    terminal_index,
                )

            if stop_index < start_index:
                continue

            pending: list[tuple[InteractionKind, IctZoneInteractionRecord]] = []

            band = ZoneBand(
                low=record.band_low,
                high=record.band_high,
            )

            polarity = ZonePolarity(record.polarity)

            for candle_index in range(
                start_index,
                stop_index + 1,
            ):
                atr = _atr_at(
                    atrs,
                    candle_index,
                )

                if atr <= _ZERO:
                    continue

                found = evaluate_zone_interaction(
                    candles[candle_index],
                    candle_index=candle_index,
                    band=band,
                    polarity=polarity,
                    atr=atr,
                )

                if not found:
                    continue

                respect = None

                for interaction in found:
                    pending.append(
                        (
                            interaction.kind,
                            self._build_interaction(
                                record,
                                interaction.kind,
                                interaction.observed_at,
                                interaction.candle_index,
                                interaction.penetration_depth,
                                interaction.close_price,
                                interaction.rejection_wick,
                                interaction.close_through,
                            ),
                        )
                    )

                    if interaction.kind is InteractionKind.RESPECT:
                        respect = interaction

                if respect is not None and lower_timeframe is not None and lower_candles:
                    confirmation = _find_ltf_confirmation(
                        lower_candles=lower_candles,
                        internal_swings=lower_internal_swings,
                        touch_at=respect.observed_at,
                        polarity=polarity,
                    )

                    if confirmation is not None:
                        bos_candle, bos_index, swing = confirmation

                        pending.append(
                            (
                                InteractionKind.CONFIRMATION,
                                self._build_confirmation(
                                    record=record,
                                    parent_index=respect.candle_index,
                                    penetration_depth=(respect.penetration_depth),
                                    rejection_wick=respect.rejection_wick,
                                    lower_timeframe=lower_timeframe,
                                    bos_candle=bos_candle,
                                    bos_index=bos_index,
                                    swing=swing,
                                ),
                            )
                        )

                if any(interaction.kind is InteractionKind.VIOLATION for interaction in found):
                    break

            # Flushed per zone rather than per interaction: one transaction
            # instead of one per row, while keeping the batch bounded by a
            # single zone's span rather than the whole window.
            written = await self._interactions.append_many([item for _, item in pending])

            for kind, item in pending:
                if item.interaction_id in written:
                    counts[kind] += 1
                    inserted_total += 1

            pending.clear()

        return IctInteractionReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            zones_evaluated=zones_evaluated,
            touches=counts[InteractionKind.TOUCH],
            rejections=counts[InteractionKind.REJECTION],
            mitigations=counts[InteractionKind.MITIGATION],
            respects=counts[InteractionKind.RESPECT],
            violations=counts[InteractionKind.VIOLATION],
            confirmations=counts[InteractionKind.CONFIRMATION],
            interactions_inserted=inserted_total,
        )

    def _build_interaction(
        self,
        record: IctZoneRecord,
        kind: InteractionKind,
        observed_at: datetime,
        candle_index: int,
        penetration_depth: Decimal,
        close_price: Decimal,
        rejection_wick: Decimal,
        close_through: bool,
    ) -> IctZoneInteractionRecord:
        evidence = json.dumps(
            {
                "algo_version": self._algo_version,
                "zone_id": record.zone_id,
                "zone_type": record.zone_type,
                "polarity": record.polarity,
                "band_low": str(record.band_low),
                "band_high": str(record.band_high),
                "zone_state": record.state,
                "kind": kind.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return IctZoneInteractionRecord(
            interaction_id=_interaction_id(
                record.zone_id,
                kind.value,
                observed_at,
            ),
            zone_id=record.zone_id,
            symbol=record.symbol,
            timeframe=record.timeframe,
            zone_type=record.zone_type,
            kind=kind.value,
            observed_at=observed_at,
            candle_index=candle_index,
            penetration_depth=penetration_depth,
            close_price=close_price,
            rejection_wick=rejection_wick,
            close_through=close_through,
            evidence=evidence,
        )

    def _build_confirmation(
        self,
        *,
        record: IctZoneRecord,
        parent_index: int,
        penetration_depth: Decimal,
        rejection_wick: Decimal,
        lower_timeframe: Timeframe,
        bos_candle: Candle,
        bos_index: int,
        swing: SwingPoint,
    ) -> IctZoneInteractionRecord:
        evidence = json.dumps(
            {
                "algo_version": self._algo_version,
                "zone_id": record.zone_id,
                "zone_type": record.zone_type,
                "kind": InteractionKind.CONFIRMATION.value,
                "parent_respect_candle_index": parent_index,
                "ltf_timeframe": lower_timeframe.value,
                "ltf_bos_index": bos_index,
                "ltf_bos_at": bos_candle.close_time.isoformat(),
                "ltf_bos_close": str(bos_candle.close),
                "ltf_swing_index": swing.index,
                "ltf_swing_price": str(swing.price),
                "ltf_swing_kind": swing.kind.value,
                "ltf_swing_strength": swing.strength.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        interaction_id = _confirmation_id(
            record.zone_id,
            bos_candle.close_time,
        )

        return IctZoneInteractionRecord(
            interaction_id=interaction_id,
            zone_id=record.zone_id,
            symbol=record.symbol,
            timeframe=record.timeframe,
            zone_type=record.zone_type,
            kind=InteractionKind.CONFIRMATION.value,
            observed_at=bos_candle.close_time,
            candle_index=parent_index,
            penetration_depth=penetration_depth,
            close_price=bos_candle.close,
            rejection_wick=rejection_wick,
            close_through=False,
            evidence=evidence,
        )


def _find_ltf_confirmation(
    *,
    lower_candles: list[Candle],
    internal_swings: tuple[SwingPoint, ...],
    touch_at: datetime,
    polarity: ZonePolarity,
) -> tuple[Candle, int, SwingPoint] | None:
    if polarity is ZonePolarity.BULLISH:
        direction = BreakDirection.UP
        required_kind = SwingKind.HIGH
    else:
        direction = BreakDirection.DOWN
        required_kind = SwingKind.LOW

    confirmation_window = swing_window(SwingStrength.INTERNAL)

    ltf_candles_seen = 0

    for candle_index, candle in enumerate(lower_candles):
        if candle.open_time < touch_at:
            continue

        ltf_candles_seen += 1

        if ltf_candles_seen > _CONFIRMATION_MAX_LTF_CANDLES:
            break

        confirmed = [
            swing
            for swing in internal_swings
            if (swing.kind is required_kind and (swing.index + confirmation_window <= candle_index))
        ]

        if not confirmed:
            continue

        swing = max(
            confirmed,
            key=lambda item: item.index,
        )

        if (
            detect_bos(
                candle,
                swing,
                direction=direction,
            )
            is not None
        ):
            return (
                candle,
                candle_index,
                swing,
            )

    return None


def _terminal_index(
    transitions: tuple[IctZoneTransitionRecord, ...],
) -> int | None:
    terminal = [
        transition.candle_index
        for transition in transitions
        if transition.to_state in _TERMINAL_STATES
    ]

    if not terminal:
        return None

    return min(terminal)


def _lower_timeframe(
    timeframe: Timeframe,
) -> Timeframe | None:
    mapping = {
        Timeframe.M5: None,
        Timeframe.M15: Timeframe.M5,
        Timeframe.H1: Timeframe.M15,
        Timeframe.H4: Timeframe.H1,
        Timeframe.D1: Timeframe.H4,
        Timeframe.W1: Timeframe.D1,
    }

    return mapping[timeframe]


def _interaction_id(
    zone_id: str,
    kind: str,
    observed_at: datetime,
) -> str:
    """Identity is the zone, the kind and the candle that made it.

    `candle_index` used to be in here, and it is a window-local number: the
    replay slides a 500-candle window forward one candle per close, so the
    same real interaction is offset 136 on one pass and 135 on the next. The
    hash changed with it, `on_conflict_do_nothing` had nothing to conflict
    with, and every pass wrote the interaction again -- once per pass, for as
    long as the candle stayed inside the window.

    Measured on the VM over 300 zones: 11,197 rows for 544 distinct
    (zone, kind, candle) triples, a factor of 20.6.

    A zone cannot be touched twice by one candle, so the triple below is the
    whole identity and two different kinds on one candle still get two rows.
    """
    raw = "|".join(
        (
            zone_id,
            kind,
            observed_at.isoformat(),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _confirmation_id(
    zone_id: str,
    bos_at: datetime,
) -> str:
    """Same correction as _interaction_id: `parent_index` was window-local."""
    raw = "|".join(
        (
            zone_id,
            InteractionKind.CONFIRMATION.value,
            bos_at.isoformat(),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atr_at(
    atrs: Sequence[Decimal | None],
    index: int,
) -> Decimal:
    """Wilder ATR at `index`, with the seeding window reported as zero.

    Reads a series computed once per run rather than calling `wilder_atr` per
    candle. That call re-seeds the recurrence from candle zero every time, so
    this loop -- every candle of every zone's span -- was quadratic: one real
    BTCUSDT H1 pass made 22 million `true_range` calls and spent about a
    minute of CPU inside them, which is most of why the engine could not keep
    up with its own seeded universe.

    Zero for the seeding window, as before: every call site here guards with
    ``if atr <= 0``, and §1.9's warm-up gate keeps production out of it.
    """

    if index < 0 or index >= len(atrs):
        return Decimal("0")

    return atrs[index] or Decimal("0")


def _empty_report(
    symbol: str,
    timeframe: Timeframe,
) -> IctInteractionReplayReport:
    return IctInteractionReplayReport(
        symbol=symbol,
        timeframe=timeframe,
        zones_evaluated=0,
        touches=0,
        rejections=0,
        mitigations=0,
        respects=0,
        violations=0,
        confirmations=0,
        interactions_inserted=0,
    )
