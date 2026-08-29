"""Chronological CHoCH/MSS replay using S4/S5 evidence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.detection.state import (
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
)
from scanner.domain.common import Candle, wilder_atr
from scanner.domain.ict import (
    DisplacementDirection,
    detect_displacement,
)
from scanner.domain.structure import (
    BreakDirection,
    ClassifiedSwing,
    MssEvidence,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    TrendState,
    TrendStateMachine,
    classify_swings,
    detect_external_swings,
    detect_internal_swings,
    evaluate_mss,
    mss_is_low_quality,
    swing_window,
)
from scanner.shared import Timeframe
from scanner.shared.errors import DomainInvariantError

# v3: two doctrine edges the machine could not express, plus one more
# window-local comparison. §3.4's recovery edge (CAUTION -> trend on a new
# confirmed HH/LL) supersedes the CHoCH and drops the MSS candidate; §3.6's
# invalidation edge (post-MSS reclaim of the pre-MSS extreme within 10
# candles) demotes the new trend to RANGING and records the MSS low_quality
# -- `mss_is_low_quality` finally has its caller. And the MSS sweep-origin
# lookback now compares the sweep's transitioned_at against candle times
# instead of a frozen recorded index against today's offsets.
STRUCTURE_SHIFT_ALGO_VERSION = "s6-structure-shift-v3"

_ATR_PERIOD = 14
_MSS_SWEEP_LOOKBACK = 10
_MSS_FOLLOWTHROUGH_MAX = 5
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class StructureShiftReplayReport:
    symbol: str
    timeframe: Timeframe
    choch_created: int
    mss_created: int
    failed_candidates: int
    events_inserted: int
    trend_state: str


@dataclass(slots=True)
class _MssCandidate:
    direction: BreakDirection
    choch_index: int
    swing_index: int
    break_extreme: Decimal
    has_displacement: bool
    has_external_sweep: bool
    has_failure_swing: bool
    # §3.6's "pre-MSS extreme (the swept low/high)": the sweep's reference
    # level under origin 2(a), the failure-swing attempt's price under 2(b).
    # Carried so the 10-candle invalidation watch has a level to test.
    pre_mss_extreme: Decimal | None


@dataclass(slots=True)
class _MssWatch:
    """§3.6's post-confirmation invalidation window."""

    direction: BreakDirection
    pre_mss_extreme: Decimal
    confirmed_index: int
    event_key: str


class StructureShiftReplayService:
    """Replay CHoCH -> MSS after liquidity evidence exists."""

    def __init__(
        self,
        candles: CandleRepository,
        events: EngineEventRepository,
        evidence: IctEvidenceRepository,
        clock: Clock,
        state: EngineStateManager,
        *,
        algo_version: str = STRUCTURE_SHIFT_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._events = events
        self._evidence = evidence
        self._clock = clock
        self._state = state
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> StructureShiftReplayReport:
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
            return StructureShiftReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                choch_created=0,
                mss_created=0,
                failed_candidates=0,
                events_inserted=0,
                trend_state=TrendState.RANGING.value,
            )

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        liquidity = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            start,
            end,
        )

        machine = TrendStateMachine()

        consumed_choch: set[
            tuple[
                int,
                SwingStrength,
            ]
        ] = set()

        candidate: _MssCandidate | None = None

        choch_created = 0
        mss_created = 0
        failed_candidates = 0
        inserted = 0

        external_window = swing_window(SwingStrength.EXTERNAL)
        internal_window = swing_window(SwingStrength.INTERNAL)

        mss_watch: _MssWatch | None = None
        previous_external_count = 0

        # §3.4's entry rule reads the last two pairs of whatever history it
        # is shown. After §3.6 demotes to RANGING, the pre-demotion pairs are
        # exactly the structure the market just proved fake -- shown again,
        # they re-enter the trend on the next candle and the demotion edge
        # means nothing. The floor hides them: re-entry needs two pairs
        # printed AFTER the demotion.
        structure_floor = 0

        for candle_index, candle in enumerate(candles):
            confirmed_external = tuple(
                swing for swing in external_swings if swing.index + external_window <= candle_index
            )

            confirmed_internal = tuple(
                swing for swing in internal_swings if swing.index + internal_window <= candle_index
            )

            external_classified = classify_swings(confirmed_external)
            internal_classified = classify_swings(confirmed_internal)

            # §3.4's entry edge, from the machine that owns it. This module
            # had its own copy of the rule and `structure_replay` had a third,
            # returning a different type -- three implementations of one line
            # of doctrine, and the one the BOS gate consulted was the one that
            # did not persist.
            machine.apply_structure(external_classified[structure_floor:])

            # §3.4's recovery edge, checked on the swings that confirmed at
            # THIS close: a new HH during BULLISH_CAUTION restores the trend
            # and supersedes the CHoCH (§3.8), so the MSS candidate is
            # dropped -- a follow-through printing later would otherwise
            # confirm an MSS from a warning the doctrine already withdrew.
            # §3.6 edge case (1) orders swing events first within a close,
            # which is why this runs before the candidate block.
            for item in external_classified[previous_external_count:]:
                if machine.apply_recovery(item.label):
                    candidate = None

            previous_external_count = len(external_classified)

            # §3.6's invalidation: within 10 candles of an MSS, a close back
            # beyond the pre-MSS extreme demotes the new trend to RANGING and
            # marks the MSS low_quality -- a fact appended, never an edit.
            if mss_watch is not None:
                since = candle_index - mss_watch.confirmed_index

                reclaimed = (
                    candle.close > mss_watch.pre_mss_extreme
                    if mss_watch.direction is BreakDirection.DOWN
                    else candle.close < mss_watch.pre_mss_extreme
                )

                if since >= 1 and mss_is_low_quality(
                    closes_back_beyond_pre_mss_extreme=reclaimed,
                    candles_since_confirmation=since,
                ):
                    machine.demote_to_ranging()
                    structure_floor = len(external_classified)

                    if await self._persist_mss_invalidation(
                        symbol=symbol,
                        timeframe=timeframe,
                        candle=candle,
                        candle_index=candle_index,
                        watch=mss_watch,
                        candles_since_confirmation=since,
                    ):
                        inserted += 1

                    mss_watch = None

                # No explicit expiry branch: `mss_is_low_quality` already
                # refuses anything past MSS_INVALIDATION_MAX, so an early
                # watch-drop was pure housekeeping -- mutating it away changed
                # no test, and redundant protection reads as load-bearing.

            if candidate is not None:
                candidate.has_displacement = (
                    candidate.has_displacement
                    or _has_directional_displacement(
                        candles,
                        candidate.direction,
                        (
                            candidate.choch_index - 1,
                            candidate.choch_index,
                            candidate.choch_index + 1,
                        ),
                        max_index=candle_index,
                    )
                )

                elapsed = candle_index - candidate.choch_index

                if 1 <= elapsed <= _MSS_FOLLOWTHROUGH_MAX:
                    has_followthrough = _has_followthrough(
                        candle,
                        candidate.direction,
                        candidate.break_extreme,
                    )

                    if has_followthrough:
                        decision = evaluate_mss(
                            MssEvidence(
                                direction=candidate.direction,
                                has_choch=True,
                                has_displacement=(candidate.has_displacement),
                                has_external_sweep=(candidate.has_external_sweep),
                                has_failure_swing=(candidate.has_failure_swing),
                                followthrough_candles=elapsed,
                            )
                        )

                        if decision.confirmed:
                            if await self._persist_mss(
                                symbol=symbol,
                                timeframe=timeframe,
                                candle=candle,
                                candle_index=candle_index,
                                candidate=candidate,
                                followthrough_candles=elapsed,
                            ):
                                inserted += 1
                                mss_created += 1

                            machine.apply_mss(
                                candidate.direction,
                            )

                            if candidate.pre_mss_extreme is not None:
                                mss_watch = _MssWatch(
                                    direction=candidate.direction,
                                    pre_mss_extreme=candidate.pre_mss_extreme,
                                    confirmed_index=candle_index,
                                    event_key=build_event_key(
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        event_type=f"MSS_{candidate.direction.value}",
                                        event_at=candle.open_time,
                                        algo_version=self._algo_version,
                                    ),
                                )

                            candidate = None

                if candidate is not None and elapsed >= _MSS_FOLLOWTHROUGH_MAX:
                    machine.fail_mss_candidate()
                    candidate = None
                    failed_candidates += 1

            if candidate is not None:
                continue

            if machine.state not in {
                TrendState.BULLISH,
                TrendState.BEARISH,
            }:
                continue

            choch = _find_choch(
                candle=candle,
                trend=machine.state,
                external_classified=external_classified,
                internal_classified=internal_classified,
                consumed=consumed_choch,
            )

            if choch is None:
                continue

            swing, direction = choch

            consumed_choch.add(
                (
                    swing.index,
                    swing.strength,
                )
            )

            if await self._persist_choch(
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
                candle_index=candle_index,
                swing=swing,
                direction=direction,
                previous_trend=machine.state,
            ):
                inserted += 1
                choch_created += 1

            machine.apply_choch(direction)

            has_external_sweep, sweep_level = _external_sweep_level(
                liquidity=liquidity,
                direction=direction,
                candles=candles,
                choch_index=candle_index,
            )

            failure_extreme = _failure_swing_extreme(
                external_classified,
                direction=direction,
                choch_index=candle_index,
            )
            has_failure_swing = failure_extreme is not None

            has_displacement = _has_directional_displacement(
                candles,
                direction,
                (
                    candle_index - 1,
                    candle_index,
                ),
                max_index=candle_index,
            )

            break_extreme = candle.low if direction is BreakDirection.DOWN else candle.high

            candidate = _MssCandidate(
                direction=direction,
                choch_index=candle_index,
                swing_index=swing.index,
                break_extreme=break_extreme,
                has_displacement=has_displacement,
                has_external_sweep=has_external_sweep,
                has_failure_swing=has_failure_swing,
                # 2(a)'s level outranks 2(b)'s: the sweep is the engineered
                # extreme the doctrine names first.
                pre_mss_extreme=(sweep_level if sweep_level is not None else failure_extreme),
            )

        # §3.7's state is what §8.2's G2 and §8.3's F6 both ask for, and F6
        # asks for it on the *timeframe above*. A value that exists only in a
        # return object cannot be read across contexts, so the ladder had no
        # way to see it and confluence scored a constant instead.
        await self._save_state(symbol, timeframe, machine.state, candles[-1].open_time)

        return StructureShiftReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            choch_created=choch_created,
            mss_created=mss_created,
            failed_candidates=failed_candidates,
            events_inserted=inserted,
            trend_state=machine.state.value,
        )

    async def _save_state(
        self,
        symbol: str,
        timeframe: Timeframe,
        trend: TrendState,
        last_open_time: datetime,
    ) -> None:
        await self._state.save(
            StructureEngineState(
                symbol=symbol,
                timeframe=timeframe.value,
                algo_version=self._algo_version,
                last_processed_open_time=last_open_time.isoformat(),
                trend_state=trend.value,
            )
        )

    async def _persist_choch(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        swing: SwingPoint,
        direction: BreakDirection,
        previous_trend: TrendState,
    ) -> bool:
        event_type = f"CHOCH_{direction.value}"

        payload = json.dumps(
            {
                "direction": direction.value,
                "break_index": candle_index,
                "candle_close": str(candle.close),
                "swing_index": swing.index,
                "swing_price": str(swing.price),
                "swing_kind": swing.kind.value,
                "swing_strength": swing.strength.value,
                "previous_trend": previous_trend.value,
                "next_trend": (
                    TrendState.BULLISH_CAUTION.value
                    if previous_trend is TrendState.BULLISH
                    else TrendState.BEARISH_CAUTION.value
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=candle.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=candle.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    async def _persist_mss_invalidation(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        watch: _MssWatch,
        candles_since_confirmation: int,
    ) -> bool:
        """§3.6: "the MSS marked `low_quality: true` (fact preserved)".

        The events table is append-only, so a fact ABOUT a prior event is a
        follow-up event carrying the original's key -- never an edit. Readers
        that grep for specific event types ignore the new one; §7.4's label
        reader skips non-classifications by construction.
        """
        event_type = f"STRUCTURE_MSS_INVALIDATED_{watch.direction.value}"

        payload = json.dumps(
            {
                "direction": watch.direction.value,
                "mss_event_key": watch.event_key,
                "pre_mss_extreme": str(watch.pre_mss_extreme),
                "candle_close": str(candle.close),
                "candles_since_confirmation": candles_since_confirmation,
                "low_quality": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=candle.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=candle.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    async def _persist_mss(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        candidate: _MssCandidate,
        followthrough_candles: int,
    ) -> bool:
        event_type = f"MSS_{candidate.direction.value}"

        payload = json.dumps(
            {
                "direction": candidate.direction.value,
                "choch_index": candidate.choch_index,
                "followthrough_index": candle_index,
                "followthrough_candles": followthrough_candles,
                "swing_index": candidate.swing_index,
                "has_displacement": candidate.has_displacement,
                "has_external_sweep": candidate.has_external_sweep,
                "has_failure_swing": candidate.has_failure_swing,
                "candle_close": str(candle.close),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=candle.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=candle.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )


def _find_choch(
    *,
    candle: Candle,
    trend: TrendState,
    external_classified: tuple[ClassifiedSwing, ...],
    internal_classified: tuple[ClassifiedSwing, ...],
    consumed: set[tuple[int, SwingStrength]],
) -> (
    tuple[
        SwingPoint,
        BreakDirection,
    ]
    | None
):
    if trend is TrendState.BULLISH:
        target_label = StructureLabel.HL
        target_kind = SwingKind.LOW
        direction = BreakDirection.DOWN

    elif trend is TrendState.BEARISH:
        target_label = StructureLabel.LH
        target_kind = SwingKind.HIGH
        direction = BreakDirection.UP

    else:
        return None

    external = [
        item.swing
        for item in external_classified
        if (
            item.label is target_label
            and item.swing.kind is target_kind
            and (
                item.swing.index,
                item.swing.strength,
            )
            not in consumed
        )
    ]

    internal = [
        item.swing
        for item in internal_classified
        if (
            item.label is target_label
            and item.swing.kind is target_kind
            and (
                item.swing.index,
                item.swing.strength,
            )
            not in consumed
        )
    ]

    candidates = external or internal

    if not candidates:
        return None

    swing = max(
        candidates,
        key=lambda item: item.index,
    )

    if direction is BreakDirection.DOWN:
        if candle.close >= swing.price:
            return None
    else:
        if candle.close <= swing.price:
            return None

    return swing, direction


def _failure_swing_extreme(
    classified: tuple[ClassifiedSwing, ...],
    *,
    direction: BreakDirection,
    choch_index: int,
) -> Decimal | None:
    """§3.6 origin 2(b): the failed attempt's price, or None.

    Returns the extreme itself rather than a bool because §3.6's invalidation
    watch needs "the pre-MSS extreme" -- for a failure-swing origin that IS
    this attempt's price.
    """
    eligible = [item for item in classified if item.swing.index < choch_index]

    if direction is BreakDirection.DOWN:
        highs = [item for item in eligible if item.swing.kind is SwingKind.HIGH]

        if highs:
            latest = max(highs, key=lambda item: item.swing.index)

            if latest.label is StructureLabel.LH:
                return latest.swing.price

        return None

    lows = [item for item in eligible if item.swing.kind is SwingKind.LOW]

    if lows:
        latest = max(lows, key=lambda item: item.swing.index)

        if latest.label is StructureLabel.HL:
            return latest.swing.price

    return None


def _external_sweep_level(
    *,
    liquidity: tuple[LiquidityEvidenceRecord, ...],
    direction: BreakDirection,
    candles: list[Candle],
    choch_index: int,
) -> tuple[bool, Decimal | None]:
    """§3.6 origin 2(a): (sweep found, its level) inside the lookback.

    Two answers, not one: the origin condition and the invalidation anchor
    are different facts. A sweep whose stored level is unreadable still
    satisfies 2(a) -- but it must NOT anchor the watch, and a zero-level
    stand-in would make a DOWN-MSS invalidate on its first candle (every
    close is above zero).

    Returns the level because §3.6's invalidation watch needs "the pre-MSS
    extreme (the swept low/high)" -- for a sweep origin that IS this level.

    Bounded in TIME: `record.candle_index` froze in whichever window recorded
    the sweep (a SWEPT pool transitions once), while `choch_index` is today's
    offset -- the same two-coordinate-system trap fixed across ob_replay,
    quietly present here too. `transitioned_at` is the sweep candle's close.
    """
    expected_side = "BSL" if direction is BreakDirection.DOWN else "SSL"

    duration = candles[choch_index].timeframe.duration
    window_opens = candles[max(0, choch_index - _MSS_SWEEP_LOOKBACK)].open_time
    choch_closes = candles[choch_index].open_time + duration

    for record in liquidity:
        if record.reason != "liquidity_sweep":
            continue

        if record.to_state != "SWEPT":
            continue

        if not (window_opens < record.transitioned_at <= choch_closes):
            continue

        # This blob is written by our own liquidity service, so a parse failure
        # is not a routine condition -- it is corruption, writer drift, or a
        # schema change nobody migrated. Swallowing it would silently answer
        # "no external sweep", which downgrades an MSS to a CHoCH and leaves no
        # trace of why. Constitution §8.5: silent failure is a violation.
        try:
            evidence = json.loads(record.evidence)
        except json.JSONDecodeError as exc:
            raise DomainInvariantError(
                "liquidity evidence is not valid JSON",
                details={
                    "pool_id": record.pool_id,
                    "candle_index": record.candle_index,
                },
            ) from exc

        if evidence.get("liquidity_class") != "EXTERNAL":
            continue

        if evidence.get("side") != expected_side:
            continue

        level = evidence.get("reference_level")

        return True, (Decimal(level) if isinstance(level, str) else None)

    return False, None


def _has_directional_displacement(
    candles: list[Candle],
    direction: BreakDirection,
    indices: tuple[int, ...],
    *,
    max_index: int,
) -> bool:
    expected = (
        DisplacementDirection.BEARISH
        if direction is BreakDirection.DOWN
        else DisplacementDirection.BULLISH
    )

    for index in indices:
        if index < 0:
            continue

        if index > max_index:
            continue

        if index >= len(candles):
            continue

        atr = _atr_at(
            candles,
            index,
        )

        displacement = detect_displacement(
            candles,
            index,
            atr=atr,
        )

        if displacement is not None and displacement.direction is expected:
            return True

    return False


def _has_followthrough(
    candle: Candle,
    direction: BreakDirection,
    break_extreme: Decimal,
) -> bool:
    if direction is BreakDirection.DOWN:
        return candle.close < break_extreme

    return candle.close > break_extreme


def _atr_at(
    candles: Sequence[Candle],
    index: int,
) -> Decimal:
    """Wilder ATR (SLS §2), with the seeding window reported as zero.

    The domain function returns None while ATR is still seeding. Every call
    site in this module already guards with ``if atr <= 0``, so zero routes to
    the same skip; this shim avoids threading Optional through them all.
    §1.9's warm-up gate keeps production out of the seeding region.
    """

    return wilder_atr(candles, index) or Decimal("0")
