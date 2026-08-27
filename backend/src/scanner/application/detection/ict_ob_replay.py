"""Order Block and Breaker replay service for Sprint S6."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import (
    CandleRepository,
    Clock,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
    ShiftEvidenceRecord,
    StructureEvidenceRecord,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneRepository,
    IctZoneStateStore,
    IctZoneTransitionRecord,
    IctZoneTransitionRepository,
)
from scanner.domain.common import Candle, wilder_atr_series
from scanner.domain.ict import (
    BreakerBlock,
    Displacement,
    DisplacementDirection,
    MitigationBlock,
    OrderBlock,
    ZoneBand,
    ZonePolarity,
    ZoneState,
    advance_breaker,
    advance_mitigation_block,
    advance_order_block,
    create_breaker,
    create_mitigation_block,
    detect_displacement,
    detect_fvg,
    detect_order_block,
)
from scanner.shared import Timeframe

ICT_OB_ALGO_VERSION = "s6-ob-v4"

_ATR_PERIOD = 14
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class IctObReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    displacements: int
    order_blocks_detected: int
    order_blocks_upserted: int
    breakers_created: int
    mitigations_created: int
    transitions: int
    live_order_blocks: int
    live_breakers: int
    live_mitigations: int
    last_processed_open_time: datetime | None


@dataclass(frozen=True, slots=True)
class StructureBreakFlags:
    internal: bool
    external: bool
    internal_level: Decimal | None
    external_level: Decimal | None

    @property
    def any_break(self) -> bool:
        return self.internal or self.external


@dataclass(frozen=True, slots=True)
class SwingEvidence:
    index: int
    price: Decimal
    strength: str
    kind: str


@dataclass(frozen=True, slots=True)
class LiquiditySweepEvidence:
    pool_id: str
    side: str
    reference_level: Decimal
    candle_index: int
    transitioned_at: datetime


class IctOrderBlockReplayService:
    """Replay deterministic OB and Breaker doctrine."""

    def __init__(
        self,
        candles: CandleRepository,
        zones: IctZoneRepository,
        transitions: IctZoneTransitionRepository,
        snapshots: IctZoneStateStore,
        evidence: IctEvidenceRepository,
        clock: Clock,
        *,
        algo_version: str = ICT_OB_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._zones = zones
        self._transitions = transitions
        self._snapshots = snapshots
        self._evidence = evidence
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> IctObReplayReport:
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
            live = await self._zones.list_live(
                symbol,
                timeframe,
            )

            await self._snapshots.save(
                symbol,
                timeframe,
                live,
            )

            return IctObReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                candles=0,
                displacements=0,
                order_blocks_detected=0,
                order_blocks_upserted=0,
                breakers_created=0,
                mitigations_created=0,
                transitions=0,
                live_order_blocks=0,
                live_breakers=0,
                live_mitigations=0,
                last_processed_open_time=None,
            )

        structure_records = await self._evidence.list_structure(
            symbol,
            timeframe,
            start,
            end,
        )

        shift_records = await self._evidence.list_shifts(
            symbol,
            timeframe,
            start,
            end,
        )

        mss_spans = _mss_spans(shift_records, timeframe)

        liquidity_records = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            start,
            end,
        )

        swings = _parse_swings(structure_records)

        sweeps = _parse_liquidity_sweeps(liquidity_records)

        # Once per run, not once per candle -- see `_atr_at`.
        atrs = wilder_atr_series(candles)

        displacements = _detect_displacements(candles, atrs)

        detected = 0
        upserted = 0

        for displacement in displacements:
            ob = self._detect_order_block(
                candles=candles,
                swings=swings,
                sweeps=sweeps,
                displacement=displacement,
                mss_spans=mss_spans,
                atrs=atrs,
            )

            if ob is None:
                continue

            detected += 1

            break_flags = _structure_break_flags(
                candles[displacement.candle_index],
                displacement,
                swings,
            )

            await self._zones.upsert(
                _order_block_record(
                    symbol=symbol,
                    timeframe=timeframe,
                    ob=ob,
                    break_flags=break_flags,
                    updated_at=self._clock.now(),
                    algo_version=(self._algo_version),
                )
            )

            upserted += 1

        live_before = await self._zones.list_live(
            symbol,
            timeframe,
        )

        transition_count = 0
        breakers_created = 0
        mitigations_created = 0

        for record in live_before:
            if record.zone_type != "OB":
                continue

            (
                transitions,
                created_breakers,
                created_mitigations,
            ) = await self._replay_ob_lifecycle(
                record=record,
                candles=candles,
                atrs=atrs,
                swings=swings,
            )

            transition_count += transitions
            breakers_created += created_breakers
            mitigations_created += created_mitigations

        live_with_breakers = await self._zones.list_live(
            symbol,
            timeframe,
        )

        for record in live_with_breakers:
            if record.zone_type == "BREAKER":
                transition_count += await self._replay_breaker_lifecycle(
                    record,
                    candles,
                )
            elif record.zone_type == "MITIGATION":
                transition_count += await self._replay_mitigation_lifecycle(
                    record,
                    candles,
                )

        live_after = await self._zones.list_live(
            symbol,
            timeframe,
        )

        await self._snapshots.save(
            symbol,
            timeframe,
            live_after,
        )

        live_ob_count = sum(1 for zone in live_after if zone.zone_type == "OB")

        live_breaker_count = sum(1 for zone in live_after if zone.zone_type == "BREAKER")

        live_mitigation_count = sum(1 for zone in live_after if zone.zone_type == "MITIGATION")

        return IctObReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=len(candles),
            displacements=len(displacements),
            order_blocks_detected=detected,
            order_blocks_upserted=upserted,
            breakers_created=(breakers_created),
            mitigations_created=(mitigations_created),
            transitions=transition_count,
            live_order_blocks=(live_ob_count),
            live_breakers=(live_breaker_count),
            live_mitigations=(live_mitigation_count),
            last_processed_open_time=(candles[-1].open_time),
        )

    def _detect_order_block(
        self,
        *,
        candles: list[Candle],
        swings: tuple[
            SwingEvidence,
            ...,
        ],
        sweeps: tuple[
            LiquiditySweepEvidence,
            ...,
        ],
        displacement: Displacement,
        mss_spans: dict[str, tuple[tuple[datetime, datetime], ...]],
        atrs: Sequence[Decimal | None],
    ) -> OrderBlock | None:
        displacement_index = displacement.candle_index

        atr = _atr_at(
            atrs,
            displacement_index,
        )

        if atr <= _ZERO:
            return None

        break_flags = _structure_break_flags(
            candles[displacement_index],
            displacement,
            swings,
        )

        fvg_created = _displacement_created_fvg(
            candles,
            displacement_index,
            atr,
        )

        if not (break_flags.any_break or fvg_created):
            return None

        candidate_start = max(
            0,
            displacement_index - 5,
        )

        for candidate_end_index in range(
            displacement_index - 1,
            candidate_start - 1,
            -1,
        ):
            provisional = detect_order_block(
                candles,
                candidate_end_index=(candidate_end_index),
                displacement=(displacement),
                atr=atr,
                external_structure_break=(break_flags.external),
                internal_structure_break=(break_flags.internal),
                # §5.1: OB_A "if the qualifying move broke external structure
                # **or the candidate sits at the origin of an MSS**". Passed as
                # a literal `False` until now, so the second half of the grade
                # rule never fired and OB_A could only ever come from an
                # external break.
                mss_origin=_within_mss(
                    mss_spans,
                    displacement,
                    candles[displacement_index].open_time,
                ),
                fvg_created=fvg_created,
                origin_swept=False,
                origin_failure_swing=False,
                stale_context=False,
            )

            if provisional is None:
                continue

            origin_swept = _origin_has_sweep(
                provisional,
                sweeps,
            )

            return replace(
                provisional,
                origin_swept=origin_swept,
            )

        return None

    async def _replay_ob_lifecycle(
        self,
        *,
        record: IctZoneRecord,
        candles: list[Candle],
        atrs: Sequence[Decimal | None],
        swings: tuple[
            SwingEvidence,
            ...,
        ],
    ) -> tuple[int, int, int]:
        current = _record_to_ob(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0, 0, 0

        transitions = 0
        breakers_created = 0
        mitigations_created = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state in {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            }:
                break

            previous_state = current.state

            updated = advance_order_block(
                current,
                candles[index],
                candle_index=index,
            )

            if updated.state is previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=(previous_state.value),
                to_state=(updated.state.value),
                reason=(_ob_transition_reason(updated.state)),
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": "OB",
                    "from_state": (previous_state.value),
                    "to_state": (updated.state.value),
                    "origin_swept": (current.origin_swept),
                    "open": str(candles[index].open),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                    "close": str(candles[index].close),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state.value,
                updated_at=(self._clock.now()),
            )

            current = updated

            if updated.state is ZoneState.INVALIDATED:
                origin_failure_swing = _has_failure_swing_before_invalidation(
                    updated,
                    swings,
                    invalidation_index=index,
                )

                promoted_ob = replace(
                    updated,
                    origin_failure_swing=origin_failure_swing,
                )

                breaker_created = await self._try_create_breaker(
                    ob=promoted_ob,
                    symbol=record.symbol,
                    timeframe=(record.timeframe),
                    candles=candles,
                    atrs=atrs,
                    swings=swings,
                    invalidation_index=index,
                )

                if breaker_created:
                    breakers_created += 1
                else:
                    mitigation_created = await self._try_create_mitigation(
                        ob=promoted_ob,
                        symbol=record.symbol,
                        timeframe=(record.timeframe),
                        candles=candles,
                        atrs=atrs,
                        swings=swings,
                        invalidation_index=index,
                    )

                    if mitigation_created:
                        mitigations_created += 1

                break

        return (
            transitions,
            breakers_created,
            mitigations_created,
        )

    async def _try_create_breaker(
        self,
        *,
        ob: OrderBlock,
        symbol: str,
        timeframe: Timeframe,
        candles: list[Candle],
        atrs: Sequence[Decimal | None],
        swings: tuple[
            SwingEvidence,
            ...,
        ],
        invalidation_index: int,
    ) -> bool:
        if not ob.origin_swept:
            return False

        atr = _atr_at(
            atrs,
            invalidation_index,
        )

        if atr <= _ZERO:
            return False

        displacement = detect_displacement(
            candles,
            invalidation_index,
            atr=atr,
        )

        if displacement is None:
            return False

        break_flags = _structure_break_flags(
            candles[invalidation_index],
            displacement,
            swings,
        )

        if not break_flags.any_break:
            return False

        gap_break = _displacement_created_fvg(
            candles,
            invalidation_index,
            atr,
        )

        breaker = create_breaker(
            ob,
            invalidation_index=(invalidation_index),
            invalidation_at=(candles[invalidation_index].close_time),
            displacement=displacement,
            structure_break=True,
            gap_break=gap_break,
        )

        if breaker is None:
            return False

        await self._zones.upsert(
            _breaker_record(
                symbol=symbol,
                timeframe=timeframe,
                breaker=breaker,
                break_flags=break_flags,
                updated_at=self._clock.now(),
                algo_version=(self._algo_version),
            )
        )

        return True

    async def _replay_breaker_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
    ) -> int:
        current = _record_to_breaker(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state in {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            }:
                break

            previous_state = current.state

            updated = advance_breaker(
                current,
                candles[index],
            )

            if updated.state is previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=(previous_state.value),
                to_state=(updated.state.value),
                reason=(_breaker_transition_reason(updated.state)),
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": ("BREAKER"),
                    "parent_ob_id": (record.parent_zone_id),
                    "from_state": (previous_state.value),
                    "to_state": (updated.state.value),
                    "open": str(candles[index].open),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                    "close": str(candles[index].close),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state.value,
                updated_at=(self._clock.now()),
            )

            current = updated

        return transitions

    async def _try_create_mitigation(
        self,
        *,
        ob: OrderBlock,
        symbol: str,
        timeframe: Timeframe,
        candles: list[Candle],
        atrs: Sequence[Decimal | None],
        swings: tuple[
            SwingEvidence,
            ...,
        ],
        invalidation_index: int,
    ) -> bool:
        if ob.origin_swept or not ob.origin_failure_swing:
            return False

        atr = _atr_at(
            atrs,
            invalidation_index,
        )

        if atr <= _ZERO:
            return False

        displacement = detect_displacement(
            candles,
            invalidation_index,
            atr=atr,
        )

        if displacement is None:
            return False

        break_flags = _structure_break_flags(
            candles[invalidation_index],
            displacement,
            swings,
        )

        if not break_flags.any_break:
            return False

        mitigation = create_mitigation_block(
            ob,
            invalidation_index=(invalidation_index),
            invalidation_at=(candles[invalidation_index].close_time),
            displacement=displacement,
            structure_break=True,
        )

        if mitigation is None:
            return False

        await self._zones.upsert(
            _mitigation_record(
                symbol=symbol,
                timeframe=timeframe,
                mitigation=mitigation,
                break_flags=break_flags,
                updated_at=self._clock.now(),
                algo_version=(self._algo_version),
            )
        )

        return True

    async def _replay_mitigation_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
    ) -> int:
        current = _record_to_mitigation(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state in {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            }:
                break

            previous_state = current.state

            updated = advance_mitigation_block(
                current,
                candles[index],
            )

            if updated.state is previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=(previous_state.value),
                to_state=(updated.state.value),
                reason=(_mitigation_transition_reason(updated.state)),
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": ("MITIGATION"),
                    "parent_ob_id": (record.parent_zone_id),
                    "from_state": (previous_state.value),
                    "to_state": (updated.state.value),
                    "open": str(candles[index].open),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                    "close": str(candles[index].close),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state.value,
                updated_at=(self._clock.now()),
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
        evidence: dict[str, object],
    ) -> bool:
        changed = await self._zones.transition(
            record.zone_id,
            from_state=from_state,
            to_state=to_state,
            updated_at=(self._clock.now()),
        )

        if not changed:
            return False

        evidence_json = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
        )

        await self._transitions.append(
            IctZoneTransitionRecord(
                transition_id=(
                    _build_transition_id(
                        zone_id=(record.zone_id),
                        from_state=(from_state),
                        to_state=to_state,
                        transitioned_at=(transitioned_at),
                    )
                ),
                zone_id=record.zone_id,
                symbol=record.symbol,
                timeframe=record.timeframe,
                zone_type=record.zone_type,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                transitioned_at=(transitioned_at),
                candle_index=(candle_index),
                evidence=evidence_json,
            )
        )

        return True


def _detect_displacements(
    candles: list[Candle],
    atrs: Sequence[Decimal | None],
) -> tuple[Displacement, ...]:
    detected: list[Displacement] = []

    for index in range(len(candles)):
        atr = _atr_at(
            atrs,
            index,
        )

        if atr <= _ZERO:
            continue

        displacement = detect_displacement(
            candles,
            index,
            atr=atr,
        )

        if displacement is not None:
            detected.append(displacement)

    return tuple(detected)


def _displacement_created_fvg(
    candles: list[Candle],
    displacement_index: int,
    atr: Decimal,
) -> bool:
    third_index = displacement_index + 1

    if third_index >= len(candles):
        return False

    fvg = detect_fvg(
        candles,
        third_index,
        atr=atr,
        middle_is_displacement=True,
    )

    return fvg is not None


def _parse_swings(
    records: tuple[
        StructureEvidenceRecord,
        ...,
    ],
) -> tuple[
    SwingEvidence,
    ...,
]:
    parsed: list[SwingEvidence] = []

    for record in records:
        if not record.event_type.startswith("SWING_"):
            continue

        raw: object = json.loads(record.payload)

        if not isinstance(
            raw,
            dict,
        ):
            continue

        index_raw = raw.get("index")
        price_raw = raw.get("price")
        strength_raw = raw.get("strength")
        kind_raw = raw.get("kind")

        if not isinstance(
            index_raw,
            int,
        ):
            continue

        if not isinstance(
            price_raw,
            str,
        ):
            continue

        if not isinstance(
            strength_raw,
            str,
        ):
            continue

        if not isinstance(
            kind_raw,
            str,
        ):
            continue

        parsed.append(
            SwingEvidence(
                index=index_raw,
                price=Decimal(price_raw),
                strength=(strength_raw),
                kind=kind_raw,
            )
        )

    parsed.sort(
        key=lambda item: (
            item.index,
            item.strength,
            item.kind,
        )
    )

    return tuple(parsed)


def _parse_liquidity_sweeps(
    records: tuple[
        LiquidityEvidenceRecord,
        ...,
    ],
) -> tuple[
    LiquiditySweepEvidence,
    ...,
]:
    parsed: list[LiquiditySweepEvidence] = []

    for record in records:
        if record.to_state != "SWEPT" or record.reason != "liquidity_sweep":
            continue

        raw: object = json.loads(record.evidence)

        if not isinstance(
            raw,
            dict,
        ):
            continue

        side_raw = raw.get("side")
        level_raw = raw.get("reference_level")

        if not isinstance(
            side_raw,
            str,
        ):
            continue

        if not isinstance(
            level_raw,
            str,
        ):
            continue

        parsed.append(
            LiquiditySweepEvidence(
                pool_id=(record.pool_id),
                side=side_raw,
                reference_level=Decimal(level_raw),
                candle_index=(record.candle_index),
                transitioned_at=(record.transitioned_at),
            )
        )

    parsed.sort(
        key=lambda item: (
            item.candle_index,
            item.pool_id,
        )
    )

    return tuple(parsed)


def _origin_has_sweep(
    ob: OrderBlock,
    sweeps: tuple[
        LiquiditySweepEvidence,
        ...,
    ],
) -> bool:
    expected_side = "SSL" if ob.polarity is ZonePolarity.BULLISH else "BSL"

    for sweep in sweeps:
        if sweep.side != expected_side:
            continue

        if not (ob.created_index <= sweep.candle_index <= ob.confirmed_index):
            continue

        if not (ob.band.low <= sweep.reference_level <= ob.band.high):
            continue

        return True

    return False


def _has_failure_swing_before_invalidation(
    ob: OrderBlock,
    swings: tuple[
        SwingEvidence,
        ...,
    ],
    *,
    invalidation_index: int,
) -> bool:
    """Return SLS §5.3 failure-swing evidence before OB invalidation."""

    required_kind = "HL" if ob.polarity is ZonePolarity.BULLISH else "LH"

    # Prefer classified structure evidence encoded in SwingEvidence.kind when
    # available. This keeps the helper compatible with richer evidence feeds.
    for swing in reversed(swings):
        if swing.index >= invalidation_index:
            continue
        if swing.index <= ob.confirmed_index:
            break
        if swing.strength == "EXTERNAL" and swing.kind == required_kind:
            return True

    # Current swing evidence stores HIGH/LOW rather than HH/HL/LH/LL.
    # Derive the failure swing deterministically from the latest two external
    # same-side pivots formed after the OB was confirmed.
    pivot_kind = "LOW" if ob.polarity is ZonePolarity.BULLISH else "HIGH"

    candidates = [
        swing
        for swing in swings
        if swing.strength == "EXTERNAL"
        and swing.kind == pivot_kind
        and ob.confirmed_index < swing.index < invalidation_index
    ]

    if len(candidates) < 2:
        return False

    candidates.sort(key=lambda item: item.index)

    previous = candidates[-2]
    latest = candidates[-1]

    if ob.polarity is ZonePolarity.BULLISH:
        return latest.price > previous.price

    return latest.price < previous.price


# §3.6 stamps MSS direction as UP/DOWN; §5.10 stamps displacement as
# BULLISH/BEARISH. Both are `str`, so comparing them directly type-checks and
# yields False forever -- the same trap that silently produced zero stop hunts
# before PR #20.
_DISPLACEMENT_TO_SHIFT: dict[str, str] = {
    DisplacementDirection.BULLISH.value: "UP",
    DisplacementDirection.BEARISH.value: "DOWN",
}


def _mss_spans(
    shifts: tuple[ShiftEvidenceRecord, ...],
    timeframe: Timeframe,
) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
    """Each MSS's confirming span, by direction, in time.

    §3.6 builds an MSS from a CHoCH plus displacement plus follow-through, so
    [choch, followthrough] is where its move happened. CHoCH records are
    ignored here: §5.1 asks about an MSS specifically.

    **In time, because the indices are window-local.** They are offsets inside
    whichever five-hundred-candle window recorded the MSS, written once and
    never revised, while the window slides one candle per close. On the host
    the stored `choch_index` runs to 499 and `followthrough_index` to 500 --
    the window's own edge -- and an ETHUSDT H1 MSS from 2026-08-19 carries 457
    against a current-window position near 317. Compared with a displacement
    indexed in *today's* window, that is two coordinate systems, and
    `_within_mss` answered false on 233 of the 234 order blocks the host has
    ever graded. §5.1's second route to OB_A, and with it §8.6 A1's "retest of
    the MSS-origin zone", were unreachable.

    Nothing new has to be persisted to fix it. `event_at` is stamped from the
    followthrough candle's `open_time`, so it dates the end of the span
    exactly; and while neither index is durable, *their difference* is -- the
    window offset is common to both and cancels. The start is that many
    candles before the end.
    """
    spans: dict[str, list[tuple[datetime, datetime]]] = {"UP": [], "DOWN": []}

    for shift in shifts:
        if not shift.event_type.startswith("MSS_"):
            continue

        if shift.direction not in spans:
            continue

        length = abs(shift.followthrough_index - shift.choch_index)

        spans[shift.direction].append(
            (
                shift.event_at - timeframe.duration * length,
                shift.event_at,
            )
        )

    return {key: tuple(value) for key, value in spans.items()}


def _within_mss(
    spans: dict[str, tuple[tuple[datetime, datetime], ...]],
    displacement: Displacement,
    at: datetime,
) -> bool:
    """Does this displacement sit inside an MSS's confirming move?

    That is what "the candidate sits at the origin of an MSS" means for an OB:
    the candidate is the footprint immediately before the displacement, so if
    the displacement is the MSS's own, the candidate is its origin.

    `at` is the displacement candle's `open_time`, not its index. See
    `_mss_spans` for why the index cannot answer this.
    """
    direction = _DISPLACEMENT_TO_SHIFT.get(displacement.direction.value)

    if direction is None:
        return False

    return any(start <= at <= end for start, end in spans.get(direction, ()))


def _structure_break_flags(
    candle: Candle,
    displacement: Displacement,
    swings: tuple[
        SwingEvidence,
        ...,
    ],
) -> StructureBreakFlags:
    internal_level = _latest_break_level(
        swings,
        strength="INTERNAL",
        direction=(displacement.direction),
        before_index=(displacement.candle_index),
    )

    external_level = _latest_break_level(
        swings,
        strength="EXTERNAL",
        direction=(displacement.direction),
        before_index=(displacement.candle_index),
    )

    internal_break = _closes_beyond_level(
        candle.close,
        displacement.direction,
        internal_level,
    )

    external_break = _closes_beyond_level(
        candle.close,
        displacement.direction,
        external_level,
    )

    return StructureBreakFlags(
        internal=internal_break,
        external=external_break,
        internal_level=(internal_level),
        external_level=(external_level),
    )


def _latest_break_level(
    swings: tuple[
        SwingEvidence,
        ...,
    ],
    *,
    strength: str,
    direction: DisplacementDirection,
    before_index: int,
) -> Decimal | None:
    required_kind = "HIGH" if direction is DisplacementDirection.BULLISH else "LOW"

    candidates = [
        swing
        for swing in swings
        if swing.strength == strength and swing.kind == required_kind and swing.index < before_index
    ]

    if not candidates:
        return None

    latest = max(
        candidates,
        key=lambda item: item.index,
    )

    return latest.price


def _closes_beyond_level(
    close: Decimal,
    direction: DisplacementDirection,
    level: Decimal | None,
) -> bool:
    if level is None:
        return False

    if direction is DisplacementDirection.BULLISH:
        return close > level

    return close < level


def _order_block_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    ob: OrderBlock,
    break_flags: StructureBreakFlags,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "OB",
            "polarity": (ob.polarity.value),
            "grade": ob.grade,
            "band_low": str(ob.band.low),
            "band_high": str(ob.band.high),
            "refined_low": str(ob.refined_band.low),
            "refined_high": str(ob.refined_band.high),
            "created_index": (ob.created_index),
            "confirmed_index": (ob.confirmed_index),
            "external_structure_break": (break_flags.external),
            "internal_structure_break": (break_flags.internal),
            "external_break_level": (
                None if break_flags.external_level is None else str(break_flags.external_level)
            ),
            "internal_break_level": (
                None if break_flags.internal_level is None else str(break_flags.internal_level)
            ),
            "mss_origin": (ob.mss_origin),
            "origin_swept": (ob.origin_swept),
            "origin_failure_swing": (ob.origin_failure_swing),
            "stale_context": (ob.stale_context),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=ob.ob_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="OB",
        polarity=ob.polarity.value,
        state=ob.state.value,
        grade=ob.grade,
        band_low=ob.band.low,
        band_high=ob.band.high,
        refined_low=(ob.refined_band.low),
        refined_high=(ob.refined_band.high),
        created_index=(ob.created_index),
        confirmed_index=(ob.confirmed_index),
        created_at=ob.created_at,
        updated_at=updated_at,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=(ob.stale_context),
        gap_adjacent=False,
        origin_swept=(ob.origin_swept),
        evidence=evidence,
    )


def _breaker_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    breaker: BreakerBlock,
    break_flags: StructureBreakFlags,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "BREAKER",
            "parent_ob_id": (breaker.parent_ob_id),
            "polarity": (breaker.polarity.value),
            "grade": breaker.grade,
            "band_low": str(breaker.band.low),
            "band_high": str(breaker.band.high),
            "refined_low": str(breaker.refined_band.low),
            "refined_high": str(breaker.refined_band.high),
            "created_index": (breaker.created_index),
            "gap_break": (breaker.gap_break),
            "external_structure_break": (break_flags.external),
            "internal_structure_break": (break_flags.internal),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=breaker.breaker_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="BREAKER",
        polarity=(breaker.polarity.value),
        state=breaker.state.value,
        grade=breaker.grade,
        band_low=breaker.band.low,
        band_high=breaker.band.high,
        refined_low=(breaker.refined_band.low),
        refined_high=(breaker.refined_band.high),
        created_index=(breaker.created_index),
        confirmed_index=(breaker.created_index),
        created_at=breaker.created_at,
        updated_at=updated_at,
        parent_zone_id=(breaker.parent_ob_id),
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=(breaker.gap_break),
        origin_swept=True,
        evidence=evidence,
    )


def _mitigation_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    mitigation: MitigationBlock,
    break_flags: StructureBreakFlags,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "MITIGATION",
            "parent_ob_id": (mitigation.parent_ob_id),
            "polarity": (mitigation.polarity.value),
            "grade": mitigation.grade,
            "band_low": str(mitigation.band.low),
            "band_high": str(mitigation.band.high),
            "refined_low": str(mitigation.refined_band.low),
            "refined_high": str(mitigation.refined_band.high),
            "created_index": (mitigation.created_index),
            "external_structure_break": (break_flags.external),
            "internal_structure_break": (break_flags.internal),
            "origin_failure_swing": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=mitigation.mitigation_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="MITIGATION",
        polarity=(mitigation.polarity.value),
        state=mitigation.state.value,
        grade=mitigation.grade,
        band_low=mitigation.band.low,
        band_high=mitigation.band.high,
        refined_low=(mitigation.refined_band.low),
        refined_high=(mitigation.refined_band.high),
        created_index=(mitigation.created_index),
        confirmed_index=(mitigation.created_index),
        created_at=mitigation.created_at,
        updated_at=updated_at,
        parent_zone_id=(mitigation.parent_ob_id),
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=False,
        evidence=evidence,
    )


def _record_to_ob(
    record: IctZoneRecord,
) -> OrderBlock:
    if record.zone_type != "OB":
        raise ValueError("record is not an OB")

    if record.refined_low is None or record.refined_high is None:
        raise ValueError("OB missing refined band")

    raw: object = json.loads(record.evidence)

    origin_failure_swing = False
    mss_origin = False

    if isinstance(
        raw,
        dict,
    ):
        value = raw.get("origin_failure_swing")

        if isinstance(
            value,
            bool,
        ):
            origin_failure_swing = value

        # Absent on zones written before the flag was stored. False is right
        # for those: their grade was computed with it False, so reading it
        # back that way keeps the reconstruction faithful to the record.
        stored = raw.get("mss_origin")

        if isinstance(
            stored,
            bool,
        ):
            mss_origin = stored

    return OrderBlock(
        ob_id=record.zone_id,
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        refined_band=ZoneBand(
            low=record.refined_low,
            high=record.refined_high,
        ),
        created_index=(record.created_index),
        confirmed_index=(record.confirmed_index),
        created_at=(record.created_at),
        grade=record.grade,
        mss_origin=mss_origin,
        origin_swept=bool(record.origin_swept),
        origin_failure_swing=(origin_failure_swing),
        stale_context=(record.stale_context),
        state=ZoneState(record.state),
    )


def _record_to_breaker(
    record: IctZoneRecord,
) -> BreakerBlock:
    if record.zone_type != "BREAKER":
        raise ValueError("record is not a breaker")

    if record.refined_low is None or record.refined_high is None:
        raise ValueError("breaker missing refined band")

    if record.parent_zone_id is None:
        raise ValueError("breaker missing parent OB")

    raw: object = json.loads(record.evidence)

    gap_break = False

    if isinstance(
        raw,
        dict,
    ):
        value = raw.get("gap_break")

        if isinstance(
            value,
            bool,
        ):
            gap_break = value

    return BreakerBlock(
        breaker_id=(record.zone_id),
        parent_ob_id=(record.parent_zone_id),
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        refined_band=ZoneBand(
            low=record.refined_low,
            high=record.refined_high,
        ),
        created_index=(record.created_index),
        created_at=(record.created_at),
        grade=record.grade,
        gap_break=gap_break,
        state=ZoneState(record.state),
    )


def _record_to_mitigation(
    record: IctZoneRecord,
) -> MitigationBlock:
    if record.zone_type != "MITIGATION":
        raise ValueError("record is not a mitigation block")

    if record.refined_low is None or record.refined_high is None:
        raise ValueError("mitigation block missing refined band")

    if record.parent_zone_id is None:
        raise ValueError("mitigation block missing parent OB")

    return MitigationBlock(
        mitigation_id=(record.zone_id),
        parent_ob_id=(record.parent_zone_id),
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        refined_band=ZoneBand(
            low=record.refined_low,
            high=record.refined_high,
        ),
        created_index=(record.created_index),
        created_at=(record.created_at),
        grade=record.grade,
        state=ZoneState(record.state),
    )


def _ob_transition_reason(
    state: ZoneState,
) -> str:
    reasons = {
        ZoneState.TESTED: ("zone_test"),
        ZoneState.MITIGATED: ("zone_mitigation"),
        ZoneState.INVALIDATED: ("close_through"),
        ZoneState.EXPIRED: ("zone_max_age"),
    }

    return reasons.get(
        state,
        "state_transition",
    )


def _breaker_transition_reason(
    state: ZoneState,
) -> str:
    reasons = {
        ZoneState.TESTED: ("breaker_test"),
        ZoneState.MITIGATED: ("breaker_mitigation"),
        ZoneState.INVALIDATED: ("breaker_failed"),
        ZoneState.EXPIRED: ("zone_max_age"),
    }

    return reasons.get(
        state,
        "state_transition",
    )


def _mitigation_transition_reason(
    state: ZoneState,
) -> str:
    reasons = {
        ZoneState.TESTED: ("mitigation_test"),
        ZoneState.MITIGATED: ("mitigation_mitigation"),
        ZoneState.INVALIDATED: ("mitigation_failed"),
        ZoneState.EXPIRED: ("zone_max_age"),
    }

    return reasons.get(
        state,
        "state_transition",
    )


def _build_transition_id(
    *,
    zone_id: str,
    from_state: str,
    to_state: str,
    transitioned_at: datetime,
) -> str:
    """See `ict_replay._build_transition_id` — the window-local index is gone."""

    raw = "|".join(
        (
            zone_id,
            from_state,
            to_state,
            transitioned_at.isoformat(),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
