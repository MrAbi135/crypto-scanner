"""Chronological CHoCH/MSS replay using S4/S5 evidence."""

from __future__ import annotations

import json
from collections.abc import Container, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

from scanner.application.detection.liquidity_replay import LIQUIDITY_ALGO_VERSION
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
from scanner.domain.common import TOLERANCE_ATR, Candle, wilder_atr, wilder_atr_series
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
from scanner.domain.structure.leg import (
    leg_origin,
    trend_start_fallback,
    trend_start_from_choch,
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
# v4: swing walk-back fix (Sec 3.1) -- the flat pause in a descent or
# ascent no longer mints a pivot, so which swings exist changes.
# v5: the walk resumes where the previous pass stopped (audit M6). It rebuilt
# the trend from RANGING at the window's first candle every pass, so the trend
# path -- and every CHoCH and MSS on it -- depended on where the window started:
# BTCUSDT H1 replayed 08-06..08-10 as BULLISH in one window and BEARISH in the
# window one candle later; offline, a sliding 150-candle window and a window
# anchored at the first candle disagreed on the trend in 59 of 300 passes. The
# snapshot is keyed by candle time, and it records the trend after each candle
# so structure's break gate can read the trend in force at a candle.
# v6: SLS v1.0.20 §3.6 (Bundle D) -- CHoCH takes §3.5's epsilon, never breaks a
# surpassed level, and breaks only inside the current leg (P1b); the snapshot
# adds the surpassed set, the leg, the last CHoCH, the leg's candles, and the
# external swings a CHoCH consumed for the structure engine's BOS. A v5
# snapshot is never read under v6 (the state key carries the version).
STRUCTURE_SHIFT_ALGO_VERSION = "s6-structure-shift-v6"

_ATR_PERIOD = 14

# A swing within this many candles of the window start may have lost the left
# side its detection needs (an external pivot needs five candles), so a resumed
# walk takes those from the snapshot instead of re-detecting them.
_SWING_HORIZON = 2 * swing_window(SwingStrength.EXTERNAL)
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

        # §3.6's sweep origin, from the running liquidity version only: after a
        # bump the previous generation's sweeps still carry the class it fixed.
        liquidity = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            start,
            end,
            only_version=LIQUIDITY_ALGO_VERSION,
        )

        machine = TrendStateMachine()

        consumed_choch: set[
            tuple[
                datetime,
                SwingStrength,
            ]
        ] = set()

        # §3.6's surpassed levels (v1.0.20): every confirmed swing some later
        # close has already passed, kept as candles are walked so that a
        # resumed pass and a whole-window pass agree.
        surpassed: set[tuple[datetime, SwingKind, SwingStrength]] = set()

        # §3.5 (v1.0.20): the external swings a CHoCH consumed and the candle
        # it consumed each on, which the structure engine's BOS reads.
        choch_consumed: dict[tuple[datetime, SwingKind], datetime] = {}

        # §3.6's current leg (v1.0.20).
        leg: _LegTrend | None = None
        turning: _TurningChoch | None = None
        carried: tuple[tuple[datetime, Decimal, Decimal], ...] = ()

        candidate: _MssCandidate | None = None

        choch_created = 0
        mss_created = 0
        failed_candidates = 0
        inserted = 0

        external_window = swing_window(SwingStrength.EXTERNAL)
        internal_window = swing_window(SwingStrength.INTERNAL)

        mss_watch: _MssWatch | None = None

        # §3.6's `- ε` (v1.0.20), derived exactly as §3.5's BOS derives it: at
        # the break candle, from a Wilder series computed once over this window
        # -- and only on a pass whose candle breaks a level at all (below).
        atrs: list[Decimal | None] | None = None

        def epsilon_at(index: int) -> Decimal:
            nonlocal atrs

            if atrs is None:
                atrs = list(wilder_atr_series(candles))

            atr = atrs[index] if index < len(atrs) else None

            return TOLERANCE_ATR * atr if atr is not None else _ZERO

        # §3.4's entry rule reads the last two pairs of whatever history it
        # is shown. After §3.6 demotes to RANGING, the pre-demotion pairs are
        # exactly the structure the market just proved fake -- shown again,
        # they re-enter the trend on the next candle and the demotion edge
        # means nothing. The floor hides them: re-entry needs two pairs
        # confirmed AFTER the demotion candle.
        floor_index = -1

        trend_path: list[tuple[datetime, TrendState]] = []
        first_index = 0

        # Resume where the previous pass stopped. A pass with no usable
        # snapshot -- a version's first, a gap longer than the window, a
        # golden run -- walks the whole window from RANGING, as every pass
        # used to.
        resumed = _resume(
            await self._state.load(symbol, timeframe.value, self._algo_version),
            candles,
        )

        if resumed is not None:
            # The window's first candles cut swings off from their left side
            # and from their predecessors, and §3.3 labels every swing against
            # its predecessor -- a window holding one external low calls it
            # SEED, and §3.4's entry rule then never sees two LL. Those swings
            # come from the snapshot, which saw them with their history.
            internal_swings = _stitch(
                resumed.swings, internal_swings, candles, SwingStrength.INTERNAL
            )
            external_swings = _stitch(
                resumed.swings, external_swings, candles, SwingStrength.EXTERNAL
            )
            machine = TrendStateMachine(state=resumed.trend)
            consumed_choch = resumed.consumed_choch
            surpassed = resumed.surpassed
            choch_consumed = resumed.choch_consumed
            leg = resumed.leg
            turning = resumed.turning
            carried = resumed.history
            candidate = resumed.candidate
            mss_watch = resumed.mss_watch
            floor_index = resumed.floor_index
            trend_path = resumed.trend_path
            first_index = resumed.next_index

        # The leg can reach back before this window, so its candles are the
        # snapshot's (lows and highs only) followed by the window's.
        before = [entry for entry in carried if entry[0] < candles[0].open_time]
        offset = len(before)
        history_times = [entry[0] for entry in before] + [c.open_time for c in candles]
        lows = [entry[1] for entry in before] + [c.low for c in candles]
        highs = [entry[2] for entry in before] + [c.high for c in candles]
        position_of = {opened: index for index, opened in enumerate(history_times)}

        for candle_index in range(first_index, len(candles)):
            candle = candles[candle_index]

            if candle_index > 0:
                _mark(trend_path, candles[candle_index - 1].open_time, machine.state)

            if candle_index > first_index:
                _mark_surpassed(
                    surpassed, candles, candle_index - 1, (*internal_swings, *external_swings)
                )

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
            machine.apply_structure(
                [
                    item
                    for item in external_classified
                    if item.swing.index + external_window > floor_index
                ]
            )

            # §3.4's recovery edge, checked on the swings that confirmed at
            # THIS close: a new HH during BULLISH_CAUTION restores the trend
            # and supersedes the CHoCH (§3.8), so the MSS candidate is
            # dropped -- a follow-through printing later would otherwise
            # confirm an MSS from a warning the doctrine already withdrew.
            # §3.6 edge case (1) orders swing events first within a close,
            # which is why this runs before the candidate block.
            for item in external_classified:
                if item.swing.index + external_window != candle_index:
                    continue

                if machine.apply_recovery(item.label):
                    candidate = None

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
                    floor_index = candle_index

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

            bullish = machine.state is TrendState.BULLISH
            current = candle_index + offset

            if leg is None or leg.trend is not machine.state:
                leg = _LegTrend(
                    trend=machine.state,
                    start_at=history_times[
                        _trend_start(
                            lows,
                            highs,
                            position_of,
                            turning=turning,
                            trend=machine.state,
                            candle=candle,
                            current=current,
                            external_classified=external_classified,
                        )
                    ],
                )

            opposite = SwingKind.HIGH if bullish else SwingKind.LOW
            origin = leg_origin(
                lows,
                highs,
                # A start older than the candles held reads from the oldest one.
                start=position_of.get(leg.start_at, 0),
                current=current,
                bullish=bullish,
                opposite=[
                    position_of[item.swing.open_time]
                    for item in external_classified
                    if item.swing.kind is opposite and item.swing.open_time in position_of
                ],
            )

            find_choch = partial(
                _find_choch,
                candle=candle,
                trend=machine.state,
                external_classified=external_classified,
                internal_classified=internal_classified,
                consumed=consumed_choch,
                surpassed=surpassed,
                leg_from=history_times[origin],
            )

            # The level is chosen before ε is applied, and ε >= 0 only ever
            # takes a break away. A candle that breaks nothing at zero breaks
            # nothing at ε, so it needs no ATR -- most candles stop here.
            choch = find_choch(epsilon=_ZERO)

            if choch is not None:
                choch = find_choch(epsilon=epsilon_at(candle_index))

            if choch is None:
                continue

            swing, direction = choch

            turning = _TurningChoch(
                trend=TrendState.BEARISH if bullish else TrendState.BULLISH,
                choch_at=candle.open_time,
                swing_at=swing.open_time,
            )

            if swing.strength is SwingStrength.EXTERNAL:
                choch_consumed.setdefault((swing.open_time, swing.kind), candle.open_time)

            consumed_choch.add(
                (
                    swing.open_time,
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

        _mark(trend_path, candles[-1].open_time, machine.state)

        # The last candle's close is applied here, so the next pass, which
        # starts after it, finds it already counted.
        if first_index < len(candles):
            _mark_surpassed(
                surpassed, candles, len(candles) - 1, (*internal_swings, *external_swings)
            )

        # §3.7's state is what §8.2's G2 and §8.3's F6 both ask for, and F6
        # asks for it on the *timeframe above*. A value that exists only in a
        # return object cannot be read across contexts, so the ladder had no
        # way to see it and confluence scored a constant instead.
        await self._save_state(
            symbol,
            timeframe,
            machine.state,
            candles[-1].open_time,
            _snapshot(
                candles,
                consumed_choch=consumed_choch,
                surpassed=surpassed,
                choch_consumed=choch_consumed,
                leg=leg,
                turning=turning,
                history=tuple(zip(history_times, lows, highs, strict=True)),
                swings=(*internal_swings, *external_swings),
                candidate=candidate,
                mss_watch=mss_watch,
                floor_index=floor_index,
                trend_path=trend_path,
            ),
        )

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
        detail: str,
    ) -> None:
        await self._state.save(
            StructureEngineState(
                symbol=symbol,
                timeframe=timeframe.value,
                algo_version=self._algo_version,
                last_processed_open_time=last_open_time.isoformat(),
                trend_state=trend.value,
                detail=detail,
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


@dataclass(frozen=True, slots=True)
class _LegTrend:
    """§3.6's trend start (v1.0.20): fixed on the first CHoCH evaluation in a
    trend direction, kept until a CHoCH is evaluated in the other one."""

    trend: TrendState
    start_at: datetime


@dataclass(frozen=True, slots=True)
class _TurningChoch:
    """The last CHoCH recorded: the trend it points to, its candle, its swing."""

    trend: TrendState
    choch_at: datetime
    swing_at: datetime


@dataclass(slots=True)
class _Resumed:
    """The walk's state after the last candle a previous pass processed,
    re-addressed to this window's candle indices."""

    trend: TrendState
    next_index: int
    consumed_choch: set[tuple[datetime, SwingStrength]] = field(default_factory=set)
    surpassed: set[tuple[datetime, SwingKind, SwingStrength]] = field(default_factory=set)
    choch_consumed: dict[tuple[datetime, SwingKind], datetime] = field(default_factory=dict)
    leg: _LegTrend | None = None
    turning: _TurningChoch | None = None
    history: tuple[tuple[datetime, Decimal, Decimal], ...] = ()
    swings: tuple[SwingPoint, ...] = ()
    candidate: _MssCandidate | None = None
    mss_watch: _MssWatch | None = None
    floor_index: int = -1
    trend_path: list[tuple[datetime, TrendState]] = field(default_factory=list)


def _mark(path: list[tuple[datetime, TrendState]], open_time: datetime, state: TrendState) -> None:
    """Record the trend after the candle opening at `open_time`, when it changed."""
    if path and (path[-1][0] >= open_time or path[-1][1] is state):
        return

    path.append((open_time, state))


def choch_consumption(detail: str | None) -> dict[tuple[datetime, SwingKind], datetime]:
    """The external swings a CHoCH consumed, each with the candle it was
    consumed on, from a saved snapshot (§3.5 v1.0.20). A snapshot without the
    field, or none at all, reads as nothing consumed."""
    if detail is None:
        return {}

    try:
        return {
            (datetime.fromisoformat(str(opened)), SwingKind(kind)): datetime.fromisoformat(
                str(taken)
            )
            for opened, kind, taken in json.loads(detail)["choch_consumed"]
        }
    except (KeyError, ValueError, TypeError):
        return {}


def trend_after(detail: str | None, open_time: datetime) -> TrendState | None:
    """The trend the shift engine held once the candle opening at `open_time`
    had closed, from a saved snapshot's path, or None when the path does not
    reach back that far (or forward: a candle the engine has not processed)."""
    if detail is None:
        return None

    try:
        path = [
            (datetime.fromisoformat(str(at)), TrendState(str(state)))
            for at, state in json.loads(detail)["trend_path"]
        ]
    except (KeyError, ValueError, TypeError):
        return None

    if not path or open_time < path[0][0]:
        return None

    found = path[0][1]

    for at, state in path:
        if at > open_time:
            break

        found = state

    return found


def _stitch(
    carried: Sequence[SwingPoint],
    detected: Sequence[SwingPoint],
    candles: Sequence[Candle],
    strength: SwingStrength,
) -> tuple[SwingPoint, ...]:
    """The swings a resumed walk reads: the snapshot's for the window's first
    `_SWING_HORIZON` candles and before them, this window's detection after.

    Carried swings are re-addressed to this window's offsets; the ones from
    before the window get negative offsets, so they stay confirmed and keep
    their place in the label sequence.
    """
    horizon = candles[min(_SWING_HORIZON, len(candles) - 1)].open_time
    start = candles[0].open_time
    step = candles[0].timeframe.duration
    index_of = {candle.open_time: index for index, candle in enumerate(candles)}

    early = [
        replace(
            swing,
            index=index_of.get(swing.open_time, int((swing.open_time - start) // step)),
        )
        for swing in carried
        if swing.strength is strength and swing.open_time < horizon
    ]
    late = [swing for swing in detected if swing.open_time >= horizon]

    return tuple(sorted((*early, *late), key=lambda swing: (swing.index, swing.kind.value)))


def _snapshot(
    candles: Sequence[Candle],
    *,
    consumed_choch: set[tuple[datetime, SwingStrength]],
    surpassed: set[tuple[datetime, SwingKind, SwingStrength]],
    choch_consumed: dict[tuple[datetime, SwingKind], datetime] | None = None,
    leg: _LegTrend | None = None,
    turning: _TurningChoch | None = None,
    history: Sequence[tuple[datetime, Decimal, Decimal]] = (),
    swings: Sequence[SwingPoint],
    candidate: _MssCandidate | None,
    mss_watch: _MssWatch | None,
    floor_index: int,
    trend_path: list[tuple[datetime, TrendState]],
) -> str:
    """The walk's state keyed by candle time, never by window offset."""

    def at(index: int) -> str | None:
        return candles[index].open_time.isoformat() if 0 <= index < len(candles) else None

    start = candles[0].open_time
    carry_from = start - candles[0].timeframe.duration * len(candles)
    before = [entry for entry in trend_path if entry[0] < start]
    path = [*before[-1:], *(entry for entry in trend_path if entry[0] >= start)]

    return json.dumps(
        {
            "consumed_choch": sorted(
                [opened.isoformat(), strength.value]
                for opened, strength in consumed_choch
                if opened >= carry_from
            ),
            "choch_consumed": sorted(
                [opened.isoformat(), kind.value, taken.isoformat()]
                for (opened, kind), taken in (choch_consumed or {}).items()
                if opened >= carry_from
            ),
            "leg": None if leg is None else [leg.trend.value, leg.start_at.isoformat()],
            "turning": (
                None
                if turning is None
                else [
                    turning.trend.value,
                    turning.choch_at.isoformat(),
                    turning.swing_at.isoformat(),
                ]
            ),
            # Lows and highs from the leg's start (or the last CHoCH's swing,
            # whichever is older), never further back than the swings carry.
            "leg_history": _history_rows(history, _history_needed(leg, turning), carry_from),
            "surpassed": sorted(
                [opened.isoformat(), kind.value, strength.value]
                for opened, kind, strength in surpassed
                if opened >= carry_from
            ),
            # Confirmed swings back to one window before this one: enough for
            # the next window's labels and entry rule, bounded in size.
            "swings": sorted(
                [
                    swing.open_time.isoformat(),
                    str(swing.price),
                    swing.kind.value,
                    swing.strength.value,
                ]
                for swing in swings
                if swing.index + swing_window(swing.strength) <= len(candles) - 1
                and swing.open_time >= carry_from
            ),
            "candidate": (
                None
                if candidate is None
                else {
                    "direction": candidate.direction.value,
                    "choch_at": at(candidate.choch_index),
                    "swing_at": at(candidate.swing_index),
                    "break_extreme": str(candidate.break_extreme),
                    "has_displacement": candidate.has_displacement,
                    "has_external_sweep": candidate.has_external_sweep,
                    "has_failure_swing": candidate.has_failure_swing,
                    "pre_mss_extreme": (
                        None
                        if candidate.pre_mss_extreme is None
                        else str(candidate.pre_mss_extreme)
                    ),
                }
            ),
            "mss_watch": (
                None
                if mss_watch is None
                else {
                    "direction": mss_watch.direction.value,
                    "pre_mss_extreme": str(mss_watch.pre_mss_extreme),
                    "confirmed_at": at(mss_watch.confirmed_index),
                    "event_key": mss_watch.event_key,
                }
            ),
            "floor_at": at(floor_index),
            "trend_path": [[opened.isoformat(), state.value] for opened, state in path],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _resume(state: StructureEngineState | None, candles: Sequence[Candle]) -> _Resumed | None:
    """Restore the walk from a snapshot whose last candle is in this window."""
    if state is None or state.last_processed_open_time is None or state.detail is None:
        return None

    index_of = {candle.open_time: index for index, candle in enumerate(candles)}

    def at(value: object) -> int | None:
        return None if value is None else index_of.get(datetime.fromisoformat(str(value)))

    try:
        last = at(state.last_processed_open_time)

        if last is None:
            return None

        detail = json.loads(state.detail)

        resumed = _Resumed(trend=TrendState(state.trend_state), next_index=last + 1)

        for opened, strength in detail["consumed_choch"]:
            resumed.consumed_choch.add(
                (datetime.fromisoformat(str(opened)), SwingStrength(strength))
            )

        for opened, kind, taken in detail["choch_consumed"]:
            resumed.choch_consumed[(datetime.fromisoformat(str(opened)), SwingKind(kind))] = (
                datetime.fromisoformat(str(taken))
            )

        raw_leg = detail["leg"]
        resumed.leg = (
            None
            if raw_leg is None
            else _LegTrend(TrendState(raw_leg[0]), datetime.fromisoformat(str(raw_leg[1])))
        )
        raw_turning = detail["turning"]
        resumed.turning = (
            None
            if raw_turning is None
            else _TurningChoch(
                TrendState(raw_turning[0]),
                datetime.fromisoformat(str(raw_turning[1])),
                datetime.fromisoformat(str(raw_turning[2])),
            )
        )
        resumed.history = tuple(
            (datetime.fromtimestamp(int(opened), tz=UTC), Decimal(low), Decimal(high))
            for opened, low, high in detail["leg_history"]
        )

        for opened, kind, strength in detail["surpassed"]:
            resumed.surpassed.add(
                (datetime.fromisoformat(str(opened)), SwingKind(kind), SwingStrength(strength))
            )

        resumed.swings = tuple(
            SwingPoint(
                index=0,
                open_time=datetime.fromisoformat(str(opened)),
                price=Decimal(str(price)),
                kind=SwingKind(kind),
                strength=SwingStrength(strength),
            )
            for opened, price, kind, strength in detail["swings"]
        )

        raw = detail["candidate"]

        if raw is not None and (choch := at(raw["choch_at"])) is not None:
            swing_at = at(raw["swing_at"])
            resumed.candidate = _MssCandidate(
                direction=BreakDirection(raw["direction"]),
                choch_index=choch,
                swing_index=-1 if swing_at is None else swing_at,
                break_extreme=Decimal(raw["break_extreme"]),
                has_displacement=bool(raw["has_displacement"]),
                has_external_sweep=bool(raw["has_external_sweep"]),
                has_failure_swing=bool(raw["has_failure_swing"]),
                pre_mss_extreme=(
                    None if raw["pre_mss_extreme"] is None else Decimal(raw["pre_mss_extreme"])
                ),
            )

        watch = detail["mss_watch"]

        if watch is not None and (confirmed := at(watch["confirmed_at"])) is not None:
            resumed.mss_watch = _MssWatch(
                direction=BreakDirection(watch["direction"]),
                pre_mss_extreme=Decimal(watch["pre_mss_extreme"]),
                confirmed_index=confirmed,
                event_key=str(watch["event_key"]),
            )

        floor = at(detail["floor_at"])
        resumed.floor_index = -1 if floor is None else floor

        resumed.trend_path = [
            (datetime.fromisoformat(str(opened)), TrendState(str(value)))
            for opened, value in detail["trend_path"]
        ]
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None

    return resumed


def _trend_start(
    lows: Sequence[Decimal],
    highs: Sequence[Decimal],
    position_of: dict[datetime, int],
    *,
    turning: _TurningChoch | None,
    trend: TrendState,
    candle: Candle,
    current: int,
    external_classified: tuple[ClassifiedSwing, ...],
) -> int:
    """§3.6's trend start (v1.0.20), as a position in the candle history."""
    bullish = trend is TrendState.BULLISH

    if turning is not None and turning.trend is trend and turning.choch_at < candle.open_time:
        choch = position_of.get(turning.choch_at)

        if choch is not None:
            return trend_start_from_choch(
                lows,
                highs,
                # A swing older than the candles held reads from the oldest one.
                swing=position_of.get(turning.swing_at, 0),
                choch=choch,
                bullish=bullish,
            )

    label = StructureLabel.HL if bullish else StructureLabel.LH

    return trend_start_fallback(
        [
            position_of[item.swing.open_time]
            for item in external_classified
            if item.label is label and item.swing.open_time in position_of
        ],
        current,
    )


def _history_needed(leg: _LegTrend | None, turning: _TurningChoch | None) -> datetime | None:
    """The oldest candle the next pass's leg can read, or None for none."""
    times = [
        *([] if leg is None else [leg.start_at]),
        *([] if turning is None else [turning.swing_at]),
    ]
    return min(times) if times else None


def _history_rows(
    history: Sequence[tuple[datetime, Decimal, Decimal]],
    needed: datetime | None,
    carry_from: datetime,
) -> list[list[object]]:
    """The leg's candles for the snapshot: epoch seconds, low, high."""
    if needed is None:
        return []

    first = max(needed, carry_from)

    return [
        [int(opened.timestamp()), str(low), str(high)]
        for opened, low, high in history
        if opened >= first
    ]


def _mark_surpassed(
    surpassed: set[tuple[datetime, SwingKind, SwingStrength]],
    candles: Sequence[Candle],
    index: int,
    swings: Sequence[SwingPoint],
) -> None:
    """Apply the close of candle `index` to §3.6's surpassed set (v1.0.20).

    A swing counts only from the candle after its confirmation candle
    (`swing.index + k`): a low is surpassed by a close below it, a high by a
    close above it. Being surpassed is permanent, so the set only grows.
    """
    close = candles[index].close

    for swing in swings:
        if swing.index + swing_window(swing.strength) >= index:
            continue

        key = (swing.open_time, swing.kind, swing.strength)

        if key in surpassed:
            continue

        if (close < swing.price) if swing.kind is SwingKind.LOW else (close > swing.price):
            surpassed.add(key)


def _find_choch(
    *,
    candle: Candle,
    trend: TrendState,
    external_classified: tuple[ClassifiedSwing, ...],
    internal_classified: tuple[ClassifiedSwing, ...],
    consumed: set[tuple[datetime, SwingStrength]],
    epsilon: Decimal,
    surpassed: Container[tuple[datetime, SwingKind, SwingStrength]],
    leg_from: datetime,
) -> (
    tuple[
        SwingPoint,
        BreakDirection,
    ]
    | None
):
    """§3.6's CHoCH on this candle, or None.

    The level is the most recent eligible swing; the close must pass it by more
    than `epsilon` (§3.6 `Cl[i] < L[swing] - ε`, v1.0.20), the same absolute
    tolerance §3.5 gives a BOS. A close within ε of the level is not a break,
    and the walk does not fall back to an older swing for it.

    A surpassed swing (v1.0.20) -- one an earlier close already passed -- is
    never the level: it was consumed silently, as §3.5 consumes BOS levels.

    Only swings at or after `leg_from`, the current leg's origin (v1.0.20), are
    eligible. External first, internal only if the leg holds no eligible
    external swing; with none at all there is no CHoCH, and the walk never
    reaches back to a swing from an earlier leg.
    """
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

    def eligible(item: ClassifiedSwing) -> bool:
        swing = item.swing
        return (
            item.label is target_label
            and swing.kind is target_kind
            and (swing.open_time, swing.strength) not in consumed
            and (swing.open_time, swing.kind, swing.strength) not in surpassed
            and swing.open_time >= leg_from
        )

    external = [item.swing for item in external_classified if eligible(item)]
    internal = [item.swing for item in internal_classified if eligible(item)]

    candidates = external or internal

    if not candidates:
        return None

    swing = max(
        candidates,
        key=lambda item: item.index,
    )

    if direction is BreakDirection.DOWN:
        if candle.close >= swing.price - epsilon:
            return None
    else:
        if candle.close <= swing.price + epsilon:
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
