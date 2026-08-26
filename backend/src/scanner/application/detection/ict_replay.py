"""ICT zone history replay service (Sprint S6)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from itertools import combinations

from scanner.application.ports import (
    CandleRepository,
    Clock,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneRepository,
    IctZoneStateStore,
    IctZoneTransitionRecord,
    IctZoneTransitionRepository,
)
from scanner.domain.common import Candle, detection_is_warm, wilder_atr_series
from scanner.domain.ict import (
    BalancedPriceRange,
    FairValueGap,
    FvgState,
    IfvgState,
    InverseFairValueGap,
    ZoneBand,
    ZonePolarity,
    advance_bpr,
    advance_fvg,
    advance_ifvg,
    compose_bpr,
    create_ifvg,
    detect_displacement,
    detect_fvg,
)
from scanner.shared import Timeframe

ICT_ALGO_VERSION = "s6-v2"

_ATR_PERIOD = 14
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class IctReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    displacements: int
    fvgs_detected: int
    ifvgs_created: int
    bprs_created: int
    zones_upserted: int
    transitions: int
    live_zones: int
    last_processed_open_time: datetime | None
    warmup_satisfied: bool = True
    """False when SLS §1.9's closed-candle floor was not met."""


class IctReplayService:
    """Replay closed candles into deterministic ICT-zone facts."""

    def __init__(
        self,
        candles: CandleRepository,
        zones: IctZoneRepository,
        transitions: IctZoneTransitionRepository,
        snapshots: IctZoneStateStore,
        clock: Clock,
        *,
        algo_version: str = ICT_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._zones = zones
        self._transitions = transitions
        self._snapshots = snapshots
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> IctReplayReport:
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

        if not detection_is_warm(len(candles)):
            await self._snapshots.save(
                symbol,
                timeframe,
                (),
            )

            return IctReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                warmup_satisfied=False,
                candles=len(candles),
                displacements=0,
                fvgs_detected=0,
                ifvgs_created=0,
                bprs_created=0,
                zones_upserted=0,
                transitions=0,
                live_zones=0,
                last_processed_open_time=(candles[-1].open_time if candles else None),
            )

        # Once per run, not once per candle -- see `_atr_at`.
        atrs = wilder_atr_series(candles)

        displacement_indices = self._detect_displacements(candles, atrs)

        detected_fvgs: list[FairValueGap] = []

        fvgs_detected = 0
        zones_upserted = 0

        for index in range(
            2,
            len(candles),
        ):
            atr = _atr_at(
                atrs,
                index,
            )

            if atr <= _ZERO:
                continue

            fvg = detect_fvg(
                candles,
                index,
                atr=atr,
                middle_is_displacement=(index - 1 in displacement_indices),
            )

            if fvg is None:
                continue

            detected_fvgs.append(fvg)
            fvgs_detected += 1

            await self._zones.upsert(
                _fvg_record(
                    symbol=symbol,
                    timeframe=timeframe,
                    fvg=fvg,
                    updated_at=self._clock.now(),
                    algo_version=self._algo_version,
                )
            )

            zones_upserted += 1

        bprs_created = await self._persist_bprs(
            symbol=symbol,
            timeframe=timeframe,
            fvgs=detected_fvgs,
            candles=candles,
        )

        zones_upserted += bprs_created

        live_before = await self._zones.list_live(
            symbol,
            timeframe,
        )

        transition_count = 0
        ifvgs_created = 0

        for record in live_before:
            if record.zone_type != "FVG":
                continue

            (
                transitions,
                created_ifvgs,
            ) = await self._replay_fvg_lifecycle(
                record,
                candles,
            )

            transition_count += transitions
            ifvgs_created += created_ifvgs
            zones_upserted += created_ifvgs

        live_derivatives = await self._zones.list_live(
            symbol,
            timeframe,
        )

        for record in live_derivatives:
            if record.zone_type == "IFVG":
                transition_count += await self._replay_ifvg_lifecycle(
                    record,
                    candles,
                    atrs,
                )

            elif record.zone_type == "BPR":
                transition_count += await self._replay_bpr_lifecycle(
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

        return IctReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=len(candles),
            displacements=len(displacement_indices),
            fvgs_detected=fvgs_detected,
            ifvgs_created=ifvgs_created,
            bprs_created=bprs_created,
            zones_upserted=zones_upserted,
            transitions=transition_count,
            live_zones=len(live_after),
            last_processed_open_time=(candles[-1].open_time),
        )

    def _detect_displacements(
        self,
        candles: list[Candle],
        atrs: Sequence[Decimal | None],
    ) -> set[int]:
        displacement_indices: set[int] = set()

        for index in range(len(candles)):
            atr = _atr_at(
                atrs,
                index,
            )

            if atr <= _ZERO:
                continue

            detected = detect_displacement(
                candles,
                index,
                atr=atr,
            )

            if detected is not None:
                displacement_indices.add(index)

        return displacement_indices

    async def _persist_bprs(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        fvgs: list[FairValueGap],
        candles: list[Candle],
    ) -> int:
        created = 0

        for first, second in combinations(
            fvgs,
            2,
        ):
            current_index = max(
                first.created_index,
                second.created_index,
            )

            if current_index < 0 or current_index >= len(candles):
                continue

            bpr = compose_bpr(
                first,
                second,
                current_index=current_index,
                created_at=(candles[current_index].close_time),
            )

            if bpr is None:
                continue

            await self._zones.upsert(
                _bpr_record(
                    symbol=symbol,
                    timeframe=timeframe,
                    bpr=bpr,
                    updated_at=(self._clock.now()),
                    algo_version=(self._algo_version),
                )
            )

            created += 1

        return created

    async def _replay_fvg_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
    ) -> tuple[int, int]:
        current = _record_to_fvg(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0, 0

        transitions = 0
        ifvgs_created = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state in {
                FvgState.FILLED,
                FvgState.INVERTED,
                FvgState.EXPIRED,
            }:
                break

            previous_state = current.state

            updated = advance_fvg(
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
                reason=(_fvg_transition_reason(updated.state)),
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": "FVG",
                    "from_state": (previous_state.value),
                    "to_state": (updated.state.value),
                    "close": str(candles[index].close),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state.value,
                updated_at=self._clock.now(),
            )

            current = updated

            if updated.state is FvgState.INVERTED:
                ifvg = create_ifvg(
                    updated,
                    inversion_index=index,
                    inversion_at=(candles[index].close_time),
                )

                await self._zones.upsert(
                    _ifvg_record(
                        symbol=record.symbol,
                        timeframe=(record.timeframe),
                        ifvg=ifvg,
                        updated_at=(self._clock.now()),
                        algo_version=(self._algo_version),
                    )
                )

                ifvgs_created += 1

        return (
            transitions,
            ifvgs_created,
        )

    async def _replay_ifvg_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
        atrs: Sequence[Decimal | None],
    ) -> int:
        current = _record_to_ifvg(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state in {
                IfvgState.DEAD,
                IfvgState.EXPIRED,
            }:
                break

            atr = _atr_at(
                atrs,
                index,
            )

            if atr <= _ZERO:
                continue

            previous_state = current.state

            updated = advance_ifvg(
                current,
                candles[index],
                candle_index=index,
                atr=atr,
            )

            if updated.state is previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=(previous_state.value),
                to_state=(updated.state.value),
                reason=(_ifvg_transition_reason(updated.state)),
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": "IFVG",
                    "parent_fvg_id": (record.parent_zone_id),
                    "from_state": (previous_state.value),
                    "to_state": (updated.state.value),
                    "close": str(candles[index].close),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                    "atr": str(atr),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state.value,
                updated_at=self._clock.now(),
            )

            current = updated

        return transitions

    async def _replay_bpr_lifecycle(
        self,
        record: IctZoneRecord,
        candles: list[Candle],
    ) -> int:
        current = _record_to_bpr(record)

        start_index = record.confirmed_index + 1

        if start_index >= len(candles):
            return 0

        transitions = 0

        for index in range(
            start_index,
            len(candles),
        ):
            if current.state == "DEAD":
                break

            previous_state = current.state

            updated = advance_bpr(
                current,
                candles[index],
            )

            if updated.state == previous_state:
                current = updated
                continue

            changed = await self._transition_zone(
                record=record,
                from_state=(previous_state),
                to_state=(updated.state),
                reason="close_through",
                candle_index=index,
                transitioned_at=(candles[index].close_time),
                evidence={
                    "algo_version": (self._algo_version),
                    "zone_id": (record.zone_id),
                    "zone_type": "BPR",
                    "from_state": (previous_state),
                    "to_state": (updated.state),
                    "close": str(candles[index].close),
                    "high": str(candles[index].high),
                    "low": str(candles[index].low),
                },
            )

            if not changed:
                break

            transitions += 1

            record = replace(
                record,
                state=updated.state,
                updated_at=self._clock.now(),
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
            updated_at=self._clock.now(),
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
                candle_index=candle_index,
                evidence=evidence_json,
            )
        )

        return True


def _fvg_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    fvg: FairValueGap,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "FVG",
            "polarity": (fvg.polarity.value),
            "band_low": str(fvg.band.low),
            "band_high": str(fvg.band.high),
            "consequent_encroachment": str(fvg.consequent_encroachment),
            "created_index": (fvg.created_index),
            "gap_adjacent": (fvg.gap_adjacent),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=fvg.fvg_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="FVG",
        polarity=fvg.polarity.value,
        state=fvg.state.value,
        grade="FVG",
        band_low=fvg.band.low,
        band_high=fvg.band.high,
        refined_low=None,
        refined_high=None,
        created_index=fvg.created_index,
        confirmed_index=(fvg.created_index),
        created_at=fvg.created_at,
        updated_at=updated_at,
        parent_zone_id=None,
        dealing_range_id=(fvg.dealing_range_id),
        stale_context=False,
        gap_adjacent=(fvg.gap_adjacent),
        origin_swept=None,
        evidence=evidence,
    )


def _ifvg_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    ifvg: InverseFairValueGap,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "IFVG",
            "parent_fvg_id": (ifvg.parent_fvg_id),
            "remaining_age": (ifvg.remaining_age),
            "polarity": (ifvg.polarity.value),
            "band_low": str(ifvg.band.low),
            "band_high": str(ifvg.band.high),
            "created_index": (ifvg.created_index),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=ifvg.ifvg_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="IFVG",
        polarity=(ifvg.polarity.value),
        state=ifvg.state.value,
        grade="IFVG",
        band_low=ifvg.band.low,
        band_high=ifvg.band.high,
        refined_low=None,
        refined_high=None,
        created_index=(ifvg.created_index),
        confirmed_index=(ifvg.created_index),
        created_at=ifvg.created_at,
        updated_at=updated_at,
        parent_zone_id=(ifvg.parent_fvg_id),
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=None,
        evidence=evidence,
    )


def _bpr_record(
    *,
    symbol: str,
    timeframe: Timeframe,
    bpr: BalancedPriceRange,
    updated_at: datetime,
    algo_version: str,
) -> IctZoneRecord:
    evidence = json.dumps(
        {
            "algo_version": (algo_version),
            "detector": "BPR",
            "parent_a_id": (bpr.parent_a_id),
            "parent_b_id": (bpr.parent_b_id),
            "polarity": (bpr.polarity.value),
            "band_low": str(bpr.band.low),
            "band_high": str(bpr.band.high),
            "created_index": (bpr.created_index),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return IctZoneRecord(
        zone_id=bpr.bpr_id,
        symbol=symbol,
        timeframe=timeframe,
        zone_type="BPR",
        polarity=bpr.polarity.value,
        state=bpr.state,
        grade="BPR",
        band_low=bpr.band.low,
        band_high=bpr.band.high,
        refined_low=None,
        refined_high=None,
        created_index=(bpr.created_index),
        confirmed_index=(bpr.created_index),
        created_at=bpr.created_at,
        updated_at=updated_at,
        parent_zone_id=(bpr.parent_a_id),
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=None,
        evidence=evidence,
    )


def _record_to_fvg(
    record: IctZoneRecord,
) -> FairValueGap:
    if record.zone_type != "FVG":
        raise ValueError("record is not an FVG")

    band = ZoneBand(
        low=record.band_low,
        high=record.band_high,
    )

    return FairValueGap(
        fvg_id=record.zone_id,
        polarity=ZonePolarity(record.polarity),
        band=band,
        consequent_encroachment=(band.midpoint),
        created_index=(record.created_index),
        created_at=record.created_at,
        dealing_range_id=(record.dealing_range_id),
        state=FvgState(record.state),
        gap_adjacent=(record.gap_adjacent),
    )


def _record_to_ifvg(
    record: IctZoneRecord,
) -> InverseFairValueGap:
    if record.zone_type != "IFVG":
        raise ValueError("record is not an IFVG")

    evidence: dict[str, object] = json.loads(record.evidence)

    remaining_age_raw = evidence.get("remaining_age")

    if not isinstance(
        remaining_age_raw,
        int,
    ):
        raise ValueError("IFVG evidence missing remaining_age")

    if record.parent_zone_id is None:
        raise ValueError("IFVG missing parent zone")

    return InverseFairValueGap(
        ifvg_id=record.zone_id,
        parent_fvg_id=(record.parent_zone_id),
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        created_index=(record.created_index),
        created_at=record.created_at,
        remaining_age=(remaining_age_raw),
        state=IfvgState(record.state),
    )


def _record_to_bpr(
    record: IctZoneRecord,
) -> BalancedPriceRange:
    if record.zone_type != "BPR":
        raise ValueError("record is not a BPR")

    evidence: dict[str, object] = json.loads(record.evidence)

    parent_a = evidence.get("parent_a_id")
    parent_b = evidence.get("parent_b_id")

    if not isinstance(
        parent_a,
        str,
    ):
        raise ValueError("BPR evidence missing parent_a_id")

    if not isinstance(
        parent_b,
        str,
    ):
        raise ValueError("BPR evidence missing parent_b_id")

    return BalancedPriceRange(
        bpr_id=record.zone_id,
        parent_a_id=parent_a,
        parent_b_id=parent_b,
        polarity=ZonePolarity(record.polarity),
        band=ZoneBand(
            low=record.band_low,
            high=record.band_high,
        ),
        created_index=(record.created_index),
        created_at=record.created_at,
        state=record.state,
    )


def _fvg_transition_reason(
    state: FvgState,
) -> str:
    reasons = {
        FvgState.TOUCHED: ("zone_touch"),
        FvgState.CE_FILLED: ("ce_fill"),
        FvgState.FILLED: ("wick_fill"),
        FvgState.INVERTED: ("close_through"),
        FvgState.EXPIRED: ("zone_max_age"),
    }

    return reasons.get(
        state,
        "state_transition",
    )


def _ifvg_transition_reason(
    state: IfvgState,
) -> str:
    reasons = {
        IfvgState.FRESH: ("successful_retest"),
        IfvgState.DEAD: ("flip_failed"),
        IfvgState.EXPIRED: ("zone_max_age"),
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
    """One transition per (zone, edge, candle), keyed on nothing that slides.

    `candle_index` was here and is gone: it is an offset inside whichever
    500-candle window observed the transition, so the same transition took a
    new id on every pass. `transitioned_at` names the candle durably, which is
    what the uniqueness was always meant to be about.
    """
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
