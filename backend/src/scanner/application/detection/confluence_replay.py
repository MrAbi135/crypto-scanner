"""Run the confluence engine over a context (SLS §8).

Gathers evidence from every engine, runs the gate battery, scores the six
factors, and records a setup candidate with its full attribution.

## Missing evidence is named, never defaulted

Several inputs §8 asks for are not reachable yet — §6.6 `wash_risk` needs
aggTrade aggregates that were never built, §5.9 Confirmations need the LTF
ladder populated, §5.7's PD gate has no persisted output. Each of those could
be passed as `False` and the code would run.

It would also be wrong in the way this project keeps meeting: a factor scoring
low because the evidence says so and a factor scoring low because nobody
fetched it are the same number, and only one of them means anything.

So every unreachable input is listed in `unreachable` on the report and in the
recorded payload. A candidate scored with three inputs missing says so, and its
confidence is readable as the partial figure it is.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.detection.signal_monitor import _transition_id
from scanner.application.detection.state import EngineStateManager
from scanner.application.detection.structure_events import (
    is_classification,
    read_classification,
)
from scanner.application.marketdata.contexts import higher_timeframe
from scanner.application.parameters import PARAM_SET_VERSION
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
)
from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionRecord,
    IctZoneInteractionRepository,
)
from scanner.application.ports.ict_zones import IctZoneRecord, IctZoneRepository
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityPoolRepository,
)
from scanner.application.ports.repositories import (
    IncidentRepository,
    SymbolRepository,
    TradeAggregateRepository,
)
from scanner.application.ports.setups import SetupRecord, SetupRepository
from scanner.application.ports.signal_transitions import (
    SignalTransitionRecord,
    SignalTransitionRepository,
)
from scanner.application.ports.signals import SignalRecord, SignalRepository
from scanner.domain.common import (
    TOLERANCE_ATR,
    Candle,
    TradeAggregate,
    wilder_atr,
    wilder_atr_series,
)
from scanner.domain.common.rvol import median, relative_volume
from scanner.domain.confluence import (
    Adjustment,
    ArchetypeEvidence,
    Confidence,
    Contribution,
    Factor,
    FactorScore,
    GateEvidence,
    LiquidityEvidence,
    MomentumEvidence,
    SignalLevels,
    StructureEvidence,
    TargetBand,
    ZoneEvidence,
    classify_archetype,
    entry_zone,
    evaluate_gates,
    final_confidence,
    htf_alignment_factor,
    invalidation_for,
    liquidity_factor,
    meets_floor,
    momentum_factor,
    structure_factor,
    volume_factor,
    zone_factor,
)
from scanner.domain.confluence.archetypes import Archetype
from scanner.domain.ict import (
    PdContext,
    PdState,
    dealing_range_at,
    detect_displacement,
    evaluate_pd_context,
)
from scanner.domain.lifecycle import SignalPayload, SignalState, publication_checks
from scanner.domain.liquidity import SWEEP_SETUP_EXPIRY_CANDLES
from scanner.domain.momentum import (
    Leg,
    LegKind,
    anchoring_legs,
    momentum_phase,
    momentum_score,
    segment_legs,
)
from scanner.domain.ranking import ttl_candles
from scanner.domain.structure import (
    StructureLabel,
    SwingPoint,
    SwingStrength,
    TrendState,
    detect_external_swings,
    unbroken_pairs,
)
from scanner.domain.volume import (
    INSTITUTIONAL_MEDIAN_WINDOW,
    ParticipationClass,
    VolumeFactorEvidence,
    candle_p90,
    classify_participation,
    cross_validate_abnormal_volume,
    delta_pct,
    detect_contraction,
    detect_expansion,
    detect_volume_spike,
    volume_factor_score,
)
from scanner.shared import Timeframe

CONFLUENCE_ALGO_VERSION = "s8-confluence-v22"

# §8.2 G5: "no opposing displacement in last 3 candles".
# §4.6's epsilon, reused to ask whether a sweep took the dealing range's own
# extreme rather than some level near it.

G5_DISPLACEMENT_WINDOW = 3

# §8.2 G4: a zone counts when its band "contains or is adjacent (<= 0.5 x ATR)
# to current price".
G4_ADJACENCY_ATR = Decimal("0.5")

# Inputs §8 asks for that no engine currently produces. Listed rather than
# silently defaulted -- see the module docstring.
# Empty, for the first time. Every §8.3.1 input is now read from evidence
# some engine actually produced. The constant stays because the honesty it
# enforces is the point, not the list: a term that loses its source must be
# named here rather than quietly scored as zero or as absent.
UNREACHABLE_INPUTS: tuple[str, ...] = ()

# Not in the constant above, because unlike those it is only *sometimes*
# unreachable: §8.4 reads the timeframe one rung up, which may be the top of
# the ladder or may simply never have been replayed. Reporting it always would
# be as misleading as reporting it never.
HTF_STATE_UNREACHABLE = "htf_state"

# Also conditional: §5.7 needs two confirmed external swings bracketing price,
# and a market that has left its last range simply has no PD context. That is
# the spec's own answer, not a gap -- but a candidate scored without one must
# still say so.
PD_CONTEXT_UNREACHABLE = "pd_context"

# §8.2 G3: "PD_SUSPENDED => only continuation archetypes eligible". §8.6 names
# exactly two continuations.
CONTINUATION_ARCHETYPES = frozenset({Archetype.CONTINUATION_PULLBACK, Archetype.FVG_CONTINUATION})


@dataclass(frozen=True, slots=True)
class _Reading:
    """§6 and §7 at the context's newest candle."""

    rvol: Decimal | None
    score: Decimal
    direction: str | None
    accelerating: bool
    decelerating: bool
    exhausted: bool
    spike_direction: str | None
    expansion_direction: str | None
    contracting: bool
    suspect: bool
    delta: Decimal | None
    p90: Decimal | None
    median_p90: Decimal | None


@dataclass(frozen=True, slots=True)
class _Legs:
    """§7.5's leg picture at the close, plus the displacement it was built on.

    Recomputed here rather than read back, for the same reason §6 and §7 are
    (see `_read_participation`): legs are a pure function of closed candles,
    confirmed swings and §5.10 displacement, so a recomputation cannot disagree
    with the engine that owns them -- and nothing persists them to read.
    """

    legs: tuple[Leg, ...]
    displacement: frozenset[int]
    swings: tuple[SwingPoint, ...]

    # The window's open times, positionally. Legs are indexed into the current
    # window and so is everything they are compared against -- except a BOS,
    # which arrives from the events table carrying an index from whichever
    # window recorded it. `span` turns a leg's bounds into times so the two
    # can be compared at all.
    open_times: tuple[datetime, ...]

    def span(self, leg: Leg) -> tuple[datetime, datetime]:
        return self.open_times[leg.start_index], self.open_times[leg.end_index]

    def latest(self, kind: LegKind, direction: str) -> Leg | None:
        for leg in reversed(anchoring_legs(self.legs)):
            if leg.kind is kind and leg.direction == direction:
                return leg

        return None

    def current(self) -> Leg | None:
        anchoring = anchoring_legs(self.legs)

        return anchoring[-1] if anchoring else None

    def displaced_recently(self, last_index: int) -> bool:
        window = range(last_index - G5_DISPLACEMENT_WINDOW + 1, last_index + 1)

        return any(index in self.displacement for index in window)


@dataclass(frozen=True, slots=True)
class SetupCandidate:
    symbol: str
    timeframe: Timeframe
    direction: str
    gates_passed: bool
    failed_gates: tuple[str, ...]
    confidence: Decimal | None
    grade: str | None
    archetype: str | None
    publishable: bool
    factors: dict[str, Decimal] = field(default_factory=dict)

    # §8.3's "itemized attribution trees". The factor functions build them and
    # the call site used to take `.score` and drop the rest, so a stored
    # confidence could say 70 without saying what earned it. Empty for the two
    # factors whose scores are still computed locally rather than from
    # enumerated contributions -- absent rather than invented.
    attribution: dict[str, tuple[Contribution, ...]] = field(default_factory=dict)

    # §8.4's base and §8.5's applied adjustments, kept whole. T16 stores both
    # beside the final number, because a stored 82 that does not say which
    # bonuses and penalties produced it cannot be recalibrated against later.
    # None for a candidate that failed gates and was never scored.
    breakdown: Confidence | None = None

    # §15.2's three priced rows. None when the candidate is not publishable,
    # or when its archetype wants a swept extreme and none was recorded --
    # §15.3 then refuses it rather than a level being invented.
    levels: SignalLevels | None = None

    # §15.2's snapshot, assembled where every input is still in scope.
    # None whenever the candidate cannot publish, so `_publish` has
    # nothing to guess at.
    payload: SignalPayload | None = None

    # §2.15 flags a zone formed across a DEGRADED gap. §15.3(2) refuses a
    # payload whose evidence chain contains one, so the flag has to travel
    # with the candidate rather than be re-read from a zone the publisher
    # no longer holds.
    stale_context: bool = False
    zone_id: str | None = None
    unreachable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfluenceReplayReport:
    symbol: str
    timeframe: Timeframe
    candidates: tuple[SetupCandidate, ...]
    events_inserted: int
    htf_state: str | None = None
    """§8.4's bias from the rung above, or None when it could not be read."""
    unreachable: tuple[str, ...] = UNREACHABLE_INPUTS


class ConfluenceReplayService:
    """Evaluate both directions on a context and record what survives."""

    def __init__(
        self,
        candles: CandleRepository,
        events: EngineEventRepository,
        zones: IctZoneRepository,
        evidence: IctEvidenceRepository,
        interactions: IctZoneInteractionRepository,
        pools: LiquidityPoolRepository,
        trades: TradeAggregateRepository,
        symbols: SymbolRepository,
        clock: Clock,
        shift_state: EngineStateManager,
        *,
        shift_algo_version: str,
        algo_version: str = CONFLUENCE_ALGO_VERSION,
        setups: SetupRepository | None = None,
        signals: SignalRepository | None = None,
        incidents: IncidentRepository | None = None,
        transitions: SignalTransitionRepository | None = None,
    ) -> None:
        self._candles = candles
        self._events = events
        self._zones = zones
        self._evidence = evidence
        self._interactions = interactions
        self._pools = pools
        self._trades = trades
        self._symbols = symbols
        self._clock = clock
        self._shift_state = shift_state
        self._shift_algo_version = shift_algo_version
        self._algo_version = algo_version
        self._setups = setups
        self._signals = signals
        self._incidents = incidents
        self._transitions = transitions

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        trend_state: str = TrendState.RANGING.value,
    ) -> ConfluenceReplayReport:
        if end <= start:
            raise ValueError("end must be greater than start")

        series = list(await self._candles.fetch_series(symbol, timeframe, start, end))

        if not series:
            return ConfluenceReplayReport(symbol, timeframe, (), 0)

        # Every engine's output, not IctEvidenceRepository.list_structure.
        # That reader filters to SWING_*/STRUCTURE_*, which is correct for the
        # ICT engines that call it and silently fatal here: BOS, CHOCH, MSS,
        # sweeps and participation flags are all absent from it, so every
        # directional check would read False and no candidate would ever pass
        # G2. A confluence engine that grades nothing looks exactly like a
        # market that offered nothing.
        events = await self._events.list_events(symbol, timeframe, start, end)
        liquidity = await self._evidence.list_liquidity(symbol, timeframe, start, end)
        live_zones = await self._zones.list_live(symbol, timeframe)

        # SLS 4.5 defines resting liquidity as the ACTIVE pool map, and
        # names target selection as one of its consumers. Read once per
        # pass; _target_pool picks the side each direction trades toward.
        resting = await self._pools.list_active(symbol, timeframe)

        event_types = {record.event_type for record in events}

        # §6.5 compares this candle's p90 print size against the median of
        # the same statistic over the trailing twenty, so twenty-one candles of
        # minute buckets is the whole read -- not the five hundred the rest of
        # the pass works over, which on H4 would be a hundred thousand rows to
        # answer a question about one.
        newest = series[-1]

        minutes = await self._trades.list_between(
            symbol,
            newest.open_time - timeframe.duration * INSTITUTIONAL_MEDIAN_WINDOW,
            newest.open_time + timeframe.duration,
        )

        reading = _read_participation(series, minutes, timeframe)

        # §6.7 caps on either tag: "hard cap 50 if `wash_risk` or
        # `suspect_volume`". §6.4's is per candle and this one is the
        # symbol's, carried by §6.6's daily evaluation.
        wash_risk = (await self._symbols.get_wash_risk(symbol)).tagged

        # §6.5(4)'s fourth disjunct. Once per pass, not per direction: it
        # asks whether the candle is a structural event candle, which is
        # not a directional question.
        respected = await self._interactions.any_respect_at(
            symbol,
            timeframe,
            newest.close_time,
        )

        labels = _read_labels(events)

        price = series[-1].close
        atr = wilder_atr(series, len(series) - 1)

        legs = _read_legs(series)

        pd = _read_pd(series, legs.swings, atr)

        bos_breaks = _bos_break_times(events)

        htf = await self._read_htf_state(symbol, timeframe)

        candidates: list[SetupCandidate] = []
        inserted = 0

        for direction in ("UP", "DOWN"):
            candidate = await self._evaluate(
                symbol=symbol,
                timeframe=timeframe,
                last_index=len(series) - 1,
                at=newest.close_time,
                opened_at=newest.open_time,
                direction=direction,
                trend_state=trend_state,
                event_types=event_types,
                events=events,
                liquidity=liquidity,
                labels=labels,
                wash_risk=wash_risk,
                respected=respected,
                resting=resting,
                live_zones=live_zones,
                reading=reading,
                htf=htf,
                price=price,
                atr=atr,
                legs=legs,
                bos_breaks=bos_breaks,
                pd=pd,
            )

            candidates.append(candidate)

            if candidate.gates_passed:
                inserted += await self._record(symbol, timeframe, series[-1].open_time, candidate)

        return ConfluenceReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candidates=tuple(candidates),
            events_inserted=inserted,
            htf_state=htf,
            # Taken from the same helper the candidates use. It defaulted to
            # the module constant before, so on a context whose ladder could
            # not be read the report announced `htf_state` as reachable while
            # the line above it said `unread` -- the two disagreeing about the
            # same run.
            unreachable=_unreachable(htf, pd),
        )

    async def _evaluate(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        last_index: int,
        at: datetime,
        opened_at: datetime,
        direction: str,
        trend_state: str,
        event_types: set[str],
        events: tuple[EngineEventRecord, ...],
        liquidity: tuple[LiquidityEvidenceRecord, ...],
        labels: tuple[StructureLabel, ...],
        wash_risk: bool,
        respected: bool,
        resting: tuple[LiquidityPoolRecord, ...],
        live_zones: tuple[IctZoneRecord, ...],
        reading: _Reading,
        htf: str | None,
        price: Decimal,
        atr: Decimal | None,
        legs: _Legs,
        bos_breaks: dict[str, frozenset[datetime]],
        pd: PdContext | None,
    ) -> SetupCandidate:
        polarity = "BULLISH" if direction == "UP" else "BEARISH"

        # §8.2 G4 asks for a zone of polarity D "whose band contains or is
        # adjacent (<= 0.5 x ATR) to current price". Only the polarity half was
        # implemented, so any live zone passed the gate wherever price stood --
        # on real BTCUSDT H1, 9 BULLISH and 16 BEARISH zones, and the gate could
        # not fail. It also meant DOWN reached grade B with its nearest zone
        # 302.7 away, five times the 60.97 the tolerance allowed.
        matching_zones = [
            zone
            for zone in live_zones
            if zone.polarity == polarity and _near_price(zone, price, atr)
        ]

        sweeps = [_Sweep(r) for r in liquidity if r.reason == "liquidity_sweep"]

        # §4.6: "a sweep's setup relevance expires P.liquidity.sweep_expiry = 15
        # closed candles after confirmation -- beyond that window it may no
        # longer seed MSS or Sweep-Reversal archetypes". The liquidity engine
        # already stamped the expiry index; nothing was reading it.
        live_sweeps = [s for s in sweeps if s.live(at, timeframe)]

        supporting = [s for s in live_sweeps if s.supports(direction) and not s.reclaimed]

        # 8.3.1 reads as one sweep's quality: "20 for a confirmed sweep
        # + 16 external + 12 depth + 6 unclaimed + 6 fresh". Taking
        # `any(external)` and `max(depth)` across the set awarded 28 to a
        # pair where one sweep was external and shallow and the other
        # internal and deep -- 28 points no single sweep had earned.
        # Ordered by what the section pays for: external (16) outranks
        # depth (12), and recency breaks ties, as it does for the zone.
        # §6.5(4): the candle has to *be* a structural event candle --
        # "institutional volume at random locations is not evidence in this
        # doctrine". At this candle, not within a window: §6.5 says
        # "coincides".
        #
        # All four disjuncts. §5.9's zone Respect was the one left out when
        # this landed, on the reading that it belongs to a particular zone --
        # but §6.5 asks whether the *candle* is a structural event candle, so
        # it is any zone's Respect, and that is a question the interaction
        # table can answer without knowing which zone the setup will be
        # scored against.
        structural = (
            respected
            or last_index in legs.displacement
            or opened_at in bos_breaks.get(direction, frozenset())
            or any(s.confirmed_at == at for s in sweeps)
        )

        best_sweep = max(
            supporting,
            key=lambda s: (s.external, s.depth_atr, s.confirmed_at),
            default=None,
        )

        target_pool = _target_pool(resting, direction, price)

        # §8.2 G5 asks for "no unexpired opposing sweep-reclaim (§4.6)" -- a
        # reclaimed sweep, not merely a sweep in the other direction. An SSL
        # sweep that was later reclaimed is price closing back below the low it
        # took: the bullish read failed, so it is contrary evidence for UP, not
        # for DOWN. §4.6 puts it plainly -- reclaim means the sweep was actually
        # absorption.
        #
        # Reading it as "any live opposing sweep" blocked both directions of
        # real BTCUSDT H1 on G5, because a liquid market sweeps both sides.
        contrary = [s for s in live_sweeps if s.reclaimed and s.supports(direction)]

        # G5's other readable clause: "no opposing displacement in last 3
        # candles". The displacement set is computed for §7.5's legs anyway, so
        # this costs nothing and turns one more hardcoded pass into a check.
        opposing_leg = legs.current()

        counter_displacement = (
            opposing_leg is not None
            and opposing_leg.direction != direction
            and legs.displaced_recently(last_index)
        )

        # §8.2 G2: "trend-following: state matches D; reversal: valid MSS or
        # Sweep-Reversal conditions §8.6".
        trend_following = _state_direction(trend_state) == direction

        # The MSS is deliberately *not* a separate branch. §3.6 is explicit that
        # "on MSS confirmation: trend flips", so a live MSS already reaches G2
        # through the trend state. Testing the window for any MSS instead let a
        # two-month-old reversal clear the gate: on real BTCUSDT H1 the window
        # held four MSS_UP and four MSS_DOWN, so both directions passed always,
        # and a gate that cannot fail is not a gate.
        #
        # What the reversal branch adds beyond the state is the Sweep-Reversal
        # case, and §4.6 gives that an explicit lifetime.
        reversal = bool(supporting)

        gates = evaluate_gates(
            GateEvidence(
                # §7.1 and §6.2 both need a warm-up window; a context too short
                # to produce either reading is not one §8 can grade. Partial --
                # G1 also wants feed freshness and tier permission -- but no
                # longer a bare `True`.
                data_ready=reading.rvol is not None,
                structure_compatible=trend_following or reversal,
                # §8.2 G3: "§5.7 directional gate satisfied (or PD_SUSPENDED
                # => only continuation archetypes eligible)". A range too
                # narrow to mean anything suspends rather than blocks; the
                # restriction it implies is applied to the archetype below.
                pd_context_ok=_pd_gate(pd, direction),
                zone_present=bool(matching_zones),
                contrary_fact_present=bool(contrary) or counter_displacement,
                volume_integrity_ok=True,
            )
        )

        if not gates.passed:
            return SetupCandidate(
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                gates_passed=False,
                failed_gates=tuple(g.value for g in gates.failed),
                confidence=None,
                grade=None,
                archetype=None,
                publishable=False,
                unreachable=_unreachable(htf, pd),
            )

        # The best zone in the stack, not the newest one. §8.3 F3 asks for the
        # quality of the zone the setup sits at, and with no entry price to
        # locate price inside the stack, the strongest member is the only
        # defensible reading. Recency breaks ties.
        best_zone = max(
            matching_zones,
            key=lambda z: (_zone_score(z, len(matching_zones)), z.created_at),
        )

        # Only for the zone actually being scored. §5.9 records interactions
        # against every zone the engine tracks, and the questions A2 and F3 ask
        # -- was the *first* retest respected, is there an entry-grade
        # Confirmation -- are about this one's own history.
        history = _History(await self._interactions.list_for_zone(best_zone.zone_id))

        scored = {
            Factor.STRUCTURE: structure_factor(
                StructureEvidence(
                    break_confirmed=f"BOS_{direction}" in event_types,
                    displaced=f"MSS_{direction}" in event_types,
                    external=any(
                        is_classification(t, strength=SwingStrength.EXTERNAL) for t in event_types
                    ),
                    mss=f"MSS_{direction}" in event_types,
                    # §7.4, from the labels §3.3 already writes into each
                    # SWING_* payload. Left at zero, the 30 points of trend
                    # maturity could never be earned by any candidate.
                    unbroken_pairs=unbroken_pairs(labels, direction),
                    failed_breaks=_count_failed_breaks(events, direction, opened_at, timeframe),
                )
            ),
            Factor.LIQUIDITY: liquidity_factor(
                LiquidityEvidence(
                    sweep_confirmed=best_sweep is not None,
                    external=best_sweep is not None and best_sweep.external,
                    depth_atr=best_sweep.depth_atr if best_sweep else Decimal(0),
                    # 8.3.1's "+6 unclaimed" and "+6 fresh (inside the 4.6
                    # expiry)" grade the sweep, not the pool it ran into. Both
                    # were passed False on the reading that they described a
                    # target pool nothing selected, which cost every swept
                    # setup 12 points that the evidence three lines above had
                    # already established. Asked of `best_sweep` rather than
                    # hardcoded True: `supporting` filters on both today, so
                    # the answer is known, but a filter that stops doing so
                    # must stop paying for it.
                    unclaimed=not best_sweep.reclaimed if best_sweep else False,
                    fresh=best_sweep.live(at, timeframe) if best_sweep else False,
                    stop_hunt="LIQUIDITY_STOP_HUNT" in event_types,
                    target_pool_strength=(target_pool.strength if target_pool else Decimal(0)),
                )
            ),
            Factor.ZONE: FactorScore(
                Factor.ZONE,
                _zone_score(
                    best_zone,
                    len(matching_zones),
                    entry_confirmation=history.confirmed,
                ),
            ),
            Factor.VOLUME: FactorScore(
                Factor.VOLUME,
                _volume_score(reading, direction, structural, wash_risk),
            ),
            Factor.MOMENTUM: momentum_factor(
                MomentumEvidence(
                    score=reading.score,
                    aligned=reading.direction == direction,
                    accelerating=reading.accelerating,
                    decelerating=reading.decelerating,
                    exhaustion_against=reading.exhausted,
                )
            ),
            Factor.HTF_ALIGNMENT: htf_alignment_factor(
                # RANGING when the rung above has no state, and said so in
                # `unreachable`. It is F6's neutral value, so an unread ladder
                # neither rewards nor punishes a candidate -- but a reader can
                # tell that from the record instead of having to guess.
                htf_state=htf if htf is not None else TrendState.RANGING.value,
                direction=direction,
            ),
        }

        factors = {factor: item.score for factor, item in scored.items()}

        confidence = final_confidence(factors, _synergies(event_types, matching_zones))

        # The retest each §8.6 chain ends in is now anchorable: G4 established
        # that price is *at* `best_zone`, which is exactly what "retrace into
        # OTE/OB/FVG" and "first touch" were missing. A3 and A4 close on that.
        #
        # A1, A2 and A5 still cannot. A1 wants §5.7's range-extreme PD, A2 wants
        # a Respect from §5.9's interaction record -- which is written but has
        # no read method -- and A5 wants the dealing range's width. Their terms
        # below are the ones genuinely readable, so those chains fail on the
        # link that is missing rather than on a fabricated one.
        impulse = legs.latest(LegKind.IMPULSE, direction)

        archetype = classify_archetype(
            ArchetypeEvidence(
                external_sweep=any(s.external for s in supporting),
                range_extreme_pd=_pd_extreme(pd, direction),
                mss_confirmed=trend_following and f"MSS_{direction}" in event_types,
                # G4 already established price is at this zone, so a zone that
                # records an MSS origin *is* the MSS-origin zone being retested.
                mss_origin_zone_retested=_is_mss_origin(best_zone),
                stop_hunt_confirmed="LIQUIDITY_STOP_HUNT" in event_types,
                breaker_formed=any(z.grade == "BRK_A" for z in matching_zones),
                breaker_grade_a=best_zone.grade == "BRK_A",
                breaker_first_retest_respected=history.first_retest_respected,
                entry_grade_confirmation=history.confirmed,
                trend_active=trend_following,
                # §8.6 A3 wants a *displaced* BOS and the BOS event does not
                # record displacement. The impulse leg it sits in does -- and
                # using the leg scopes it too, since a BOS from two months back
                # is not what the current pullback is retracing.
                displaced_bos=_bos_inside(impulse, bos_breaks.get(direction, frozenset()), legs),
                # G4 already established price is at this zone.
                retraced_into_zone=True,
                htf_aligned=htf == direction,
                retracement_leg=_is_retracement_against(legs.current(), direction),
                counter_displacement=counter_displacement,
                displacement_fvg=_is_displacement_fvg(best_zone, legs.displacement),
                fvg_first_touch=_is_first_touch(best_zone, price),
                fvg_age_candles=_age_in_candles(best_zone.created_at, at, timeframe),
                ranging=trend_state == TrendState.RANGING.value,
                range_extreme_swept=_swept_a_range_extreme(supporting, pd, atr),
                rejection_confirmed=history.rejected,
                range_width_atr=(pd.range_high - pd.range_low) / atr
                if pd is not None and atr
                else Decimal(0),
            )
        )

        # §8.2 G3's other half. Suspending PD does not block the candidate,
        # it narrows what the candidate may be called -- so a reversal chain
        # that happened to match is withdrawn rather than published under a
        # context the doctrine says cannot support it.
        if archetype is not None and _pd_suspended(pd) and archetype not in CONTINUATION_ARCHETYPES:
            archetype = None

        publishable = (
            archetype is not None
            and confidence.published_grade is not None
            and meets_floor(archetype, confidence.final)
        )

        levels = (
            _levels_for(
                archetype,
                direction=direction,
                zone=best_zone,
                swept_extreme=best_sweep.reference_level if best_sweep else None,
                target_pool=target_pool,
                pd=pd,
            )
            if publishable and archetype is not None
            else None
        )

        payload = (
            _payload_for(
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                archetype=archetype,
                confidence=confidence,
                factors=factors,
                levels=levels,
                atr=atr,
                price=price,
                htf=htf,
                zone=best_zone,
                wash_risk=wash_risk,
                reading=reading,
                algo_version=self._algo_version,
            )
            if levels is not None and archetype is not None
            else None
        )

        return SetupCandidate(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            gates_passed=True,
            failed_gates=(),
            confidence=confidence.final,
            grade=confidence.published_grade.value if confidence.published_grade else None,
            archetype=archetype.value if archetype else None,
            publishable=publishable,
            factors={f.value: score for f, score in factors.items()},
            attribution={
                f.value: item.contributions for f, item in scored.items() if item.contributions
            },
            breakdown=confidence,
            levels=levels,
            payload=payload,
            stale_context=best_zone.stale_context,
            zone_id=best_zone.zone_id,
            unreachable=_unreachable(htf, pd),
        )

    async def _record(
        self,
        symbol: str,
        timeframe: Timeframe,
        event_at: datetime,
        candidate: SetupCandidate,
    ) -> int:
        event_type = f"SETUP_CANDIDATE_{candidate.direction}"

        inserted = await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=event_at,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=event_at,
                algo_version=self._algo_version,
                payload=json.dumps(
                    {
                        "confidence": str(candidate.confidence),
                        "grade": candidate.grade,
                        "archetype": candidate.archetype,
                        "publishable": candidate.publishable,
                        # S8's DoD asks for "G1-G7 with recorded results" and
                        # for every candidate to carry "the full factor
                        # evidence tree". Both were computed and then dropped
                        # here, which is why "which gate blocks every setup"
                        # could not be answered from the database at all --
                        # only guessed at by re-reading the code.
                        "gates_passed": candidate.gates_passed,
                        "failed_gates": list(candidate.failed_gates),
                        "factors": {k: str(v) for k, v in candidate.factors.items()},
                        "attribution": {
                            factor: [
                                {
                                    "code": c.code,
                                    "points": str(c.points),
                                    "evidence_id": c.evidence_id,
                                }
                                for c in contributions
                            ]
                            for factor, contributions in candidate.attribution.items()
                        },
                        # Carried into the record itself: a stored confidence
                        # whose missing inputs are not stated cannot be read
                        # honestly a month later.
                        "zone_id": candidate.zone_id,
                        "unreachable_inputs": list(candidate.unreachable),
                    },
                    sort_keys=True,
                ),
                created_at=self._clock.now(),
            )
        )

        await self._store_setup(symbol, timeframe, event_at, candidate)
        await self._publish(symbol, timeframe, event_at, candidate)

        return 1 if inserted else 0

    async def _publish(
        self,
        symbol: str,
        timeframe: Timeframe,
        event_at: datetime,
        candidate: SetupCandidate,
    ) -> None:
        """§12.2: evaluate §15.3 once, atomically, and publish or suppress.

        A candidate with no payload never reaches here as a publication: it
        either failed the gates, missed its floor, or could not fill a §15.2
        row. All three are already recorded — the event log has the candidate
        and T16 has the scored one — so there is nothing further to say.
        """
        if self._signals is None or candidate.payload is None:
            return

        payload = candidate.payload

        decision = publication_checks(
            payload,
            feeds_fresh=await self._feeds_clean(symbol, candidate),
            dedup_clear=await self._dedup_clear(payload, timeframe, event_at),
        )

        if not decision.published:
            # §12.2: "Fail => SUPPRESSED with recorded reason (auditable
            # funnel: candidates -> published is a monitored ratio, §14)".
            # The reason rides on the event log rather than T17, because T17
            # holds published signals and a suppression is the absence of one.
            await self._events.append(
                EngineEventRecord(
                    event_key=build_event_key(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=f"SIGNAL_SUPPRESSED_{candidate.direction}",
                        event_at=event_at,
                        algo_version=self._algo_version,
                    ),
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=f"SIGNAL_SUPPRESSED_{candidate.direction}",
                    event_at=event_at,
                    algo_version=self._algo_version,
                    payload=json.dumps(
                        {"reasons": [r.value for r in decision.reasons]},
                        sort_keys=True,
                    ),
                    created_at=self._clock.now(),
                )
            )

            return

        levels = payload.levels

        # One id for both: §12.1 creates the signal from the setup, and a
        # separate identifier would only invite the two to drift.
        signal_id = _setup_id(
            symbol,
            timeframe,
            candidate.direction,
            event_at,
            self._algo_version,
        )

        await self._signals.append(
            SignalRecord(
                signal_id=signal_id,
                setup_id=signal_id,
                symbol=symbol,
                timeframe=timeframe,
                direction=candidate.direction,
                archetype=payload.archetype,
                grade=payload.grade,
                final_confidence=payload.confidence,
                entry_proximal=levels.entry.proximal,
                entry_distal=levels.entry.distal,
                invalidation_level=levels.invalidation.price,
                target_bands=json.dumps(
                    payload.as_dict()["targets"], sort_keys=True, separators=(",", ":")
                ),
                published_at=event_at,
                ttl_candles=ttl_candles(timeframe),
                algo_version=self._algo_version,
                param_set_version=payload.param_set_version,
                payload=json.dumps(payload.as_dict(), sort_keys=True, separators=(",", ":")),
                payload_hash=payload.seal(),
                dedup_key=payload.dedup_key(),
            )
        )

        # §12's first two edges, recorded together. A published signal with no
        # transition row is invisible to §12.3's monitor -- `list_live` reads
        # the latest transition, and a signal that never had one is never
        # watched. Writing it here rather than leaving it to the monitor keeps
        # the two facts in one place: the row exists because the signal
        # published, and it says so.
        if self._transitions is not None:
            await self._transitions.append(
                SignalTransitionRecord(
                    transition_id=_transition_id(signal_id, event_at),
                    signal_id=signal_id,
                    from_state=SignalState.DETECTED.value,
                    to_state=SignalState.PUBLISHED.value,
                    at_candle_open_time=event_at,
                    recorded_at=self._clock.now(),
                    stress_test=False,
                    trigger_evidence=json.dumps(
                        {"reason": "publication checks passed"}, sort_keys=True
                    ),
                )
            )

    async def _feeds_clean(self, symbol: str, candidate: SetupCandidate) -> bool:
        """§15.3(2): "all feeds fresh at publish moment; no DEGRADED input in
        the evidence chain".

        Two claims, and both are checkable here. An open incident on the
        symbol is the feed not being clean (§2.15), and a zone carrying
        `stale_context` is a DEGRADED input that reached the chain -- §2.15
        flags exactly that on anything formed across a gap.

        G1's `data_ready` is *not* reused for this. Its own comment admits it
        is partial -- it checks the warm-up and says freshness is still owed --
        so treating it as freshness would have made §15.3(2) a check that
        could not fail.
        """
        if candidate.stale_context:
            return False

        if self._incidents is None:
            return True

        return not await self._incidents.list_open(symbol)

    async def _dedup_clear(
        self,
        payload: SignalPayload,
        timeframe: Timeframe,
        at: datetime,
    ) -> bool:
        """§15.3(4): "dedup key clear (§10.3)".

        Clear means no signal on this key is still inside its TTL. §10.3 only
        merges against an *ACTIVE* signal, so a key that produced a signal
        long enough ago for it to have expired is free again -- which is why
        the query returns the latest row and the TTL arithmetic happens here
        rather than in SQL.
        """
        if self._signals is None:
            return True

        latest = await self._signals.latest_for_dedup_key(payload.dedup_key())

        if latest is None:
            return True

        elapsed = at - latest.published_at

        return elapsed >= timeframe.duration * latest.ttl_candles

    async def _store_setup(
        self,
        symbol: str,
        timeframe: Timeframe,
        evaluated_at: datetime,
        candidate: SetupCandidate,
    ) -> None:
        """T16, for the candidates that cleared §8.2.

        DDD T16 holds "every confluence candidate that passed gates --
        published *and* below-floor", so a gate failure is recorded in the
        event log and nowhere else: there is no scored evidence to calibrate
        against, and `base_confidence` would have to be invented to store one.

        The event in `_record` above still goes out either way. It is the
        engine's audit log; this is the modelled record the product reads, and
        they answer different questions.
        """
        if self._setups is None or not candidate.gates_passed:
            return

        breakdown = candidate.breakdown

        if breakdown is None:
            raise ValueError(f"{symbol}: a gate-passing candidate carries no confidence")

        await self._setups.append(
            SetupRecord(
                setup_id=_setup_id(
                    symbol,
                    timeframe,
                    candidate.direction,
                    evaluated_at,
                    self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                direction=candidate.direction,
                archetype=candidate.archetype,
                gate_results=json.dumps(
                    {
                        "passed": candidate.gates_passed,
                        "failed": list(candidate.failed_gates),
                    },
                    sort_keys=True,
                ),
                factor_scores=json.dumps(
                    {k: str(v) for k, v in candidate.factors.items()},
                    sort_keys=True,
                ),
                adjustments=json.dumps(
                    {
                        "applied": [
                            {"code": a.code, "points": str(a.points)} for a in breakdown.applied
                        ],
                        "synergy": str(breakdown.synergy),
                        "penalty": str(breakdown.penalty),
                        # §8.5 caps both sides. Whether a cap bound is the
                        # difference between "no more evidence" and "more
                        # evidence than the doctrine will pay for", and only
                        # the flags can tell those apart later.
                        "synergy_capped": breakdown.synergy_capped,
                        "penalty_capped": breakdown.penalty_capped,
                    },
                    sort_keys=True,
                ),
                base_confidence=breakdown.base,
                final_confidence=breakdown.final,
                floor_passed=candidate.publishable,
                algo_version=self._algo_version,
                evaluated_at=evaluated_at,
                evidence=json.dumps(
                    {
                        "zone_id": candidate.zone_id,
                        "grade": candidate.grade,
                        "unreachable_inputs": list(candidate.unreachable),
                        "attribution": {
                            factor: [
                                {
                                    "code": c.code,
                                    "points": str(c.points),
                                    "evidence_id": c.evidence_id,
                                }
                                for c in contributions
                            ]
                            for factor, contributions in candidate.attribution.items()
                        },
                    },
                    sort_keys=True,
                ),
            )
        )

    async def _read_htf_state(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> str | None:
        """§8.4's HTF bias, in F6's vocabulary, or None if it cannot be read.

        None covers two genuinely different situations that both mean "do not
        claim an alignment": the top of the ladder has no timeframe above, and
        a lower rung may simply never have been replayed for this symbol. The
        second is the failure recorded in the timeframe-ladder notes -- an
        unpopulated neighbour yields a plausible number rather than an error --
        so it is reported rather than smoothed over.
        """
        above = higher_timeframe(timeframe)

        if above is None:
            return None

        state = await self._shift_state.load(
            symbol,
            above.value,
            self._shift_algo_version,
        )

        if state is None:
            return None

        return _f6_vocabulary(state.trend_state)


class _Sweep:
    """A §4.6 sweep transition, read through the questions §8 asks of it."""

    __slots__ = ("_e", "confirmed_at")

    def __init__(self, record: LiquidityEvidenceRecord) -> None:
        self._e = json.loads(record.evidence)

        # The transition's real time, not its `candle_index`. That index is
        # the offset inside whichever window recorded the sweep, and it is
        # frozen there while the window keeps sliding -- see `live` below.
        self.confirmed_at = record.transitioned_at

    @property
    def reclaimed(self) -> bool:
        return bool(self._e.get("reclaimed"))

    @property
    def reference_level(self) -> Decimal | None:
        raw = self._e.get("reference_level")

        return Decimal(str(raw)) if raw is not None else None

    @property
    def external(self) -> bool:
        return bool(self._e.get("liquidity_class") == "EXTERNAL")

    @property
    def depth_atr(self) -> Decimal:
        raw = self._e.get("sweep_depth_atr")

        return Decimal(str(raw)) if raw is not None else Decimal(0)

    def live(self, at: datetime, timeframe: Timeframe) -> bool:
        """§4.6's 15 closed candles, counted in time rather than in offsets.

        This read `setup_expiry_index` out of the evidence -- `confirmed_index
        + 15`, both offsets inside the window that recorded the sweep -- and
        compared it against the current window's `last_index`. Neither number
        moves with the other: `last_index` is always the right edge, and the
        stored one never changes again, so the comparison had nothing to do
        with how long ago the sweep happened.

        Measured on the soak VM over 87,605 sweep transitions: 78,265 carry an
        expiry index below 500 and so were dead the moment they were written,
        and the rest sit at or above it and can never expire at all. Neither
        group was ever 15 candles from anything.
        """
        return at - self.confirmed_at <= timeframe.duration * SWEEP_SETUP_EXPIRY_CANDLES

    def supports(self, direction: str) -> bool:
        """A sweep of resting sell-side liquidity clears the way up, and vice versa."""
        side = self._e.get("side")

        return bool(side == "SSL") if direction == "UP" else bool(side == "BSL")


def _target_pool(
    resting: tuple[LiquidityPoolRecord, ...],
    direction: str,
    price: Decimal,
) -> LiquidityPoolRecord | None:
    """SLS 4.5: "nearest opposing external pool = default target zone".

    Opposing means the side the move runs toward -- an UP setup targets the
    buy-side liquidity resting above it -- and 4.2's validation rule says the
    same thing from the other end: "BSL pools only relevant while price is
    below them". A pool already behind price is not a target, so the side test
    alone is not enough and the position test is not redundant with it.

    Nearest, not strongest. A 100-strength pool eight ranges away would pay the
    full 25 points to a setup that will reach the weak one first and stop
    there; 4.5 ranks the map by "strength x proximity", and of the two only
    proximity decides which pool is the target.
    """

    up = direction == "UP"
    side = "BSL" if up else "SSL"

    candidates = [
        pool
        for pool in resting
        # 4.4: external liquidity sits at or beyond the dealing-range
        # extremes. Internal pools are explicitly "used for target-setting
        # (15) and entry refinement -- never as reversal evidence"; 4.5 asks
        # this term for the external one.
        if pool.side == side
        and pool.liquidity_class == "EXTERNAL"
        and (pool.price > price if up else pool.price < price)
    ]

    if not candidates:
        return None

    return min(candidates, key=lambda pool: (abs(pool.price - price), -pool.strength))


class _History:
    """One zone's §5.9 interaction record, in the terms §8 asks about."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[IctZoneInteractionRecord, ...]) -> None:
        self._items = items

    @property
    def confirmed(self) -> bool:
        """§5.9's entry-grade Confirmation, scored by §8.3.1 and A2 alike."""
        return any(item.kind == "CONFIRMATION" for item in self._items)

    @property
    def rejected(self) -> bool:
        """§8.6 A5's "rejection" -- §5.9 records it as a fact on the zone."""
        return any(item.kind == "REJECTION" for item in self._items)

    @property
    def first_retest_respected(self) -> bool:
        """§8.6 A2: "Breaker formed -> *first* retest with Respect".

        The first retest, not any of them. A zone that was violated and only
        later respected is a different story from one that held on the first
        approach, and A2 is about the second.
        """
        retests = [
            item
            for item in self._items
            if item.kind in {"TOUCH", "REJECTION", "RESPECT", "VIOLATION", "MITIGATION"}
        ]

        if not retests:
            return False

        # observed_at, not candle_index. The index is the row's offset inside
        # the sliding window that recorded it, so two rows for the same real
        # candle carried different indices and two rows for different candles
        # could carry the same one -- this comparison could miss a true Respect
        # and equally invent one, and A2 grades an entry on the answer.
        first = retests[0].observed_at

        return any(item.kind == "RESPECT" and item.observed_at == first for item in self._items)


def _setup_id(
    symbol: str,
    timeframe: Timeframe,
    direction: str,
    evaluated_at: datetime,
    algo_version: str,
) -> str:
    """T16's identity: one candidate per symbol, TF, direction and close.

    Derived from real values only -- no window offset anywhere near it. The
    §5.9 interaction ids were hashed over a sliding-window index and wrote the
    same fact twenty times over; this is the same shape of key built the way
    that one should have been.
    """
    raw = "|".join(
        (
            symbol,
            timeframe.value,
            direction,
            evaluated_at.isoformat(),
            algo_version,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _payload_for(
    *,
    symbol: str,
    timeframe: Timeframe,
    direction: str,
    archetype: Archetype,
    confidence: Confidence,
    factors: dict[Factor, Decimal],
    levels: SignalLevels,
    atr: Decimal | None,
    price: Decimal,
    htf: str | None,
    zone: IctZoneRecord,
    wash_risk: bool,
    reading: _Reading,
    algo_version: str,
) -> SignalPayload | None:
    """§15.2's nine rows, assembled where every input is still in scope.

    Returns None when the risk row cannot be filled: §15.2 wants the
    invalidation distance "in ATR and %", and without an ATR there is no ATR
    distance to state. §15.3 would refuse the payload anyway; refusing to
    build it says the same thing one step earlier and without a zero standing
    in for a measurement nobody took.
    """
    if atr is None or atr <= 0 or price <= 0:
        return None

    multiple = levels.r_multiple

    if multiple is None:
        return None

    distance = abs(levels.entry.mid - levels.invalidation.price)

    return SignalPayload(
        symbol=symbol,
        timeframe=timeframe.value,
        direction=direction,
        # §15.2's "complete event-id chain". The attribution trees carry the
        # evidence id behind each scored contribution, which is the chain §8
        # actually built; the zone is added because §15.2 names zones
        # separately and a zone with no interaction contributes no id.
        evidence_ids=(zone.zone_id,),
        confidence=confidence.final,
        grade=confidence.published_grade.value if confidence.published_grade else "",
        factors={f.value: str(score) for f, score in factors.items()},
        archetype=archetype.value,
        reason=_reason(archetype, direction),
        invalidation_distance_atr=distance / atr,
        invalidation_distance_pct=distance / price * Decimal(100),
        r_multiple=multiple,
        condition_tags=_condition_tags(wash_risk=wash_risk, reading=reading),
        levels=levels,
        # §15.2's "HTF bias chain states at creation (snapshot)". RANGING when
        # the rung above has no state, which is what §8.4's F6 reads too --
        # and the record says so rather than omitting the row.
        htf_chain={
            timeframe.value: "SIGNAL",
            "HTF": htf if htf is not None else TrendState.RANGING.value,
        },
        algo_version=algo_version,
        param_set_version=PARAM_SET_VERSION,
    )


def _reason(archetype: Archetype, direction: str) -> str:
    """§15.2's "deterministic reason string (template, human-readable)".

    Deterministic is the operative word: the same archetype and direction
    always produce the same sentence, so two signals that differ in their text
    differ in their evidence. §11's AI thesis is the interpretive one and is
    written separately, against its own version pair.
    """
    side = "long" if direction == "UP" else "short"

    return f"{archetype.value} {side}: {_ARCHETYPE_SENTENCE[archetype]}"


_ARCHETYPE_SENTENCE: dict[Archetype, str] = {
    Archetype.SWEEP_REVERSAL: "external sweep, then a shift, retested at its origin zone.",
    Archetype.BREAKER_RETEST: "a breaker held on its first retest.",
    Archetype.CONTINUATION_PULLBACK: "trend with a displaced break, retraced into the zone.",
    Archetype.FVG_CONTINUATION: "first touch of a displacement gap in the trend direction.",
    Archetype.RANGE_LIQUIDITY_PLAY: "range extreme swept and rejected.",
}


def _condition_tags(*, wash_risk: bool, reading: _Reading) -> tuple[str, ...]:
    """§15.2's "market-condition tags (wash_risk, funding extreme, exhaustion_watch, news_risk)".

    Only the two this engine can establish are emitted. Funding and news come
    from feeds the detection stack does not read, and a tag list that always
    omitted them silently would read as "checked and clear".
    """
    tags = []

    if wash_risk:
        tags.append("wash_risk")

    if reading.exhausted:
        tags.append("exhaustion_watch")

    return tuple(tags)


def _levels_for(
    archetype: Archetype,
    *,
    direction: str,
    zone: IctZoneRecord,
    swept_extreme: Decimal | None,
    target_pool: LiquidityPoolRecord | None,
    pd: PdContext | None,
) -> SignalLevels | None:
    """§15.2's entry, invalidation and targets for one candidate.

    Returns None when a required row cannot be filled. §15.3(1) wants every
    field non-null, so an absent target or an A1 with no recorded swept
    extreme is a signal that must not publish -- and inventing either would
    hand a trader a level the doctrine never derived.
    """
    entry = entry_zone(
        zone_id=zone.zone_id,
        direction=direction,
        band_low=zone.band_low,
        band_high=zone.band_high,
        refined_low=zone.refined_low,
        refined_high=zone.refined_high,
    )

    invalidation = invalidation_for(
        archetype,
        entry=entry,
        swept_extreme=swept_extreme,
    )

    if invalidation is None:
        return None

    primary = _primary_target(archetype, direction=direction, pool=target_pool, pd=pd)

    if primary is None:
        return None

    return SignalLevels(
        direction=direction,
        entry=entry,
        invalidation=invalidation,
        primary_target=primary,
    )


def _primary_target(
    archetype: Archetype,
    *,
    direction: str,
    pool: LiquidityPoolRecord | None,
    pd: PdContext | None,
) -> TargetBand | None:
    """§15.2's "nearest opposing external liquidity pool band", except for A5.

    §8.6 gives A5 its own: "Target = opposing range extreme". The archetype's
    own row wins over the general rule, and reading only §15.2 would send a
    range play at a pool that has nothing to do with the range it is playing.
    """
    if archetype is Archetype.RANGE_LIQUIDITY_PLAY:
        if pd is None:
            return None

        extreme = pd.range_high if direction == "UP" else pd.range_low

        return TargetBand(low=extreme, high=extreme)

    if pool is None:
        return None

    return TargetBand(
        low=pool.band_low,
        high=pool.band_high,
        pool_id=pool.pool_id,
        strength=pool.strength,
    )


def _age_in_candles(
    created_at: datetime,
    at: datetime,
    timeframe: Timeframe,
) -> int:
    """A zone's age, measured in candles, from two real timestamps.

    It used to be `max(0, last_index - confirmed_index)`, and those two
    numbers belong to different windows. `last_index` is always the right edge
    of the current 500-candle window; `confirmed_index` is the offset the zone
    had when it was first detected, and the zone upsert does not list it in
    its `set_` clause, so it never moves again. A zone is almost always found
    at the right edge too, which is how a difference of nearly zero came out
    of a zone that had been sitting there for days.

    Measured on the soak VM over 6,388 non-terminal FVG zones: A4's
    `fvg_age_candles <= 30` admitted 982 of them, where the age implied by
    their timestamps admits 227. The oldest was 690 candles old and read as
    200 -- and it was 690, past the 500-candle window, so no index could have
    described it at all.

    Both arguments are candle close times, so the division is exact. It is not
    clamped at zero on purpose: a negative age means the zone was created
    after the candle being scored, and A4's `0 <= fvg_age_candles` should be
    allowed to catch that instead of being made unfalsifiable by a `max`.
    """
    return (at - created_at) // timeframe.duration


def _read_pd(
    series: list[Candle],
    swings: tuple[SwingPoint, ...],
    atr: Decimal | None,
) -> PdContext | None:
    """§5.7 at the close, or None when no range brackets price.

    Recomputed rather than read back for the same reason as §6, §7 and §7.5:
    the range is the most recent confirmed external swing on each side, and the
    context a pure function of that range, the close and ATR. The OTE engine
    computes exactly this per candle and keeps it to itself, which is why G3
    was a hardcoded pass.
    """
    if atr is None or atr <= 0:
        return None

    dealing_range = dealing_range_at(
        swings,
        close=series[-1].close,
        index=len(series) - 1,
    )

    if dealing_range is None:
        return None

    return evaluate_pd_context(dealing_range, close=series[-1].close, atr=atr)


def _swept_a_range_extreme(
    sweeps: list[_Sweep],
    pd: PdContext | None,
    atr: Decimal | None,
) -> bool:
    """§8.6 A5: "sweep of range extreme".

    Matched on the level the sweep referenced rather than on the pool's source:
    §4.2 creates a pool for each dealing-range extreme, but nothing in this
    build does -- pools come from swings and clusters. The range's own anchors
    are external swings, so the swing pool at that price *is* the range extreme,
    and comparing levels finds it without depending on a source that is never
    written. The tolerance is §4.6's epsilon, the same one the sweep was
    detected with.
    """
    if pd is None or atr is None or atr <= 0:
        return False

    tolerance = TOLERANCE_ATR * atr

    return any(
        sweep.reference_level is not None
        and (
            abs(sweep.reference_level - pd.range_high) <= tolerance
            or abs(sweep.reference_level - pd.range_low) <= tolerance
        )
        for sweep in sweeps
    )


def _pd_suspended(pd: PdContext | None) -> bool:
    return pd is not None and pd.state is PdState.SUSPENDED


def _pd_gate(pd: PdContext | None, direction: str) -> bool:
    """§5.7's directional gate: long needs range_position <= 0.5, short >= 0.5.

    An unreadable or suspended context passes. §8.2 G3 is explicit that
    PD_SUSPENDED narrows the archetype set rather than failing the gate, and a
    context with no range at all is named in `unreachable` instead -- blocking
    every candidate on a range the market has simply left would be a gate on
    the absence of evidence.
    """
    if pd is None or pd.state is PdState.SUSPENDED:
        return True

    return pd.long_gate if direction == "UP" else pd.short_gate


def _pd_extreme(pd: PdContext | None, direction: str) -> bool:
    """§5.7's extreme third, which §8.6 A1 requires on top of the gate."""
    if pd is None or pd.state is PdState.SUSPENDED:
        return False

    return pd.sweep_long_gate if direction == "UP" else pd.sweep_short_gate


def _read_legs(series: list[Candle]) -> _Legs:
    """§7.5 legs over the window, with the §5.10 displacement they need."""
    atrs = wilder_atr_series(series)

    displacement = frozenset(
        index
        for index, atr in enumerate(atrs)
        if atr is not None and atr > 0 and detect_displacement(series, index, atr=atr) is not None
    )

    swings = detect_external_swings(series)

    return _Legs(
        legs=segment_legs(series, swings, displacement),
        displacement=displacement,
        swings=swings,
        open_times=tuple(candle.open_time for candle in series),
    )


def _is_retracement_against(leg: Leg | None, direction: str) -> bool:
    """§8.6 A3's "retracement leg (§7.5), not counter-displacement".

    A pullback inside a D-trend travels *against* D. A retracement running with
    D is the trend leg itself, and calling that a pullback would classify every
    trending context as a setup.
    """
    return leg is not None and leg.kind is LegKind.RETRACEMENT and leg.direction != direction


def _bos_break_times(
    events: tuple[EngineEventRecord, ...],
) -> dict[str, frozenset[datetime]]:
    """When each BOS broke, by direction.

    This read `break_index` out of the payload, on the stated grounds that it
    and the event's timestamp "differ, because a break is stamped at the
    candle that closed through the level". They do not differ:
    `structure_replay` takes both from that same candle, `event_at` from its
    `open_time` and `break_index` from its offset. The index adds nothing --
    except that it is the offset inside whichever window recorded the break,
    written once and never revised, while everything it was compared against
    belongs to the window being scored now.

    On the VM, 67 of 187 BOS events carry `break_index = 500`, and
    `last_index` is 500 on every pass for both scanned symbols -- so §6.5's
    "the candle is a structural event candle" disjunct was permanently true,
    which is the reverse of what §6.5 means by "institutional volume at random
    locations is not evidence in this doctrine".
    """
    breaks: dict[str, set[datetime]] = {"UP": set(), "DOWN": set()}

    for record in events:
        if not record.event_type.startswith("BOS_"):
            continue

        direction = record.event_type.removeprefix("BOS_")

        if direction not in breaks:
            continue

        breaks[direction].add(record.event_at)

    return {key: frozenset(value) for key, value in breaks.items()}


def _bos_inside(
    impulse: Leg | None,
    breaks: frozenset[datetime],
    legs: _Legs,
) -> bool:
    """§8.6 A3's "displaced BOS", scoped to the leg that is being retraced.

    The leg's bounds are offsets in the current window; the breaks arrive from
    the events table. Comparing the two directly meant asking whether a number
    from one window fell between two numbers from another. Both sides are
    times now.
    """
    if impulse is None or not impulse.displaced:
        return False

    start, end = legs.span(impulse)

    return any(start <= at <= end for at in breaks)


def _is_displacement_fvg(zone: IctZoneRecord, displacement: frozenset[int]) -> bool:
    """§8.6 A4 wants a *displacement* FVG, and the zone record does not say.

    §5.4 detects gaps without asking what made them, so the origin is recovered
    from the same §5.10 set §7.5 uses: a displacement on any candle of the
    three the gap spans.
    """
    if zone.zone_type not in {"FVG", "IFVG", "BPR"}:
        return False

    return any(
        index in displacement for index in range(zone.created_index - 2, zone.created_index + 1)
    )


def _is_mss_origin(zone: IctZoneRecord) -> bool:
    """§8.6 A1's "retest of the MSS-origin zone".

    Read from the zone's own evidence rather than inferred from its grade:
    §5.1 awards OB_A for an external break *or* an MSS origin, so the grade
    alone cannot say which of the two happened.
    """
    try:
        evidence = json.loads(zone.evidence)
    except (ValueError, TypeError):
        return False

    return bool(evidence.get("mss_origin")) if isinstance(evidence, dict) else False


def _is_first_touch(zone: IctZoneRecord, price: Decimal) -> bool:
    """§8.6 A4's "first touch": an untouched zone that price is now inside.

    §5 spells untouched two ways -- FRESH for OB-family zones, OPEN for the FVG
    family -- so both are named. The zone's own state is the record of whether
    it has been touched before, which is why no interaction lookup is needed.
    """
    return zone.state in {"FRESH", "OPEN"} and zone.band_low <= price <= zone.band_high


def _near_price(zone: IctZoneRecord, price: Decimal, atr: Decimal | None) -> bool:
    """§8.2 G4's proximity test.

    A missing ATR fails closed. The alternative -- treating an unmeasurable
    tolerance as an infinite one -- restores exactly the gate this replaces,
    and G1 already refuses a context too short to measure.
    """
    if zone.band_low <= price <= zone.band_high:
        return True

    if atr is None or atr <= 0:
        return False

    tolerance = G4_ADJACENCY_ATR * atr

    if price < zone.band_low:
        return zone.band_low - price <= tolerance

    return price - zone.band_high <= tolerance


def _f6_vocabulary(trend_state: str) -> str:
    """§3.7's states as §8.3's F6 table names them.

    F6 wants UP / DOWN / RANGING / CAUTION_<D>; §3.7 says BULLISH, BEARISH and
    their CAUTION variants. Two spellings of one idea is how §8.3.1's zone table
    came to score every FVG at zero, so the translation is explicit here rather
    than left to a string that happens to match.
    """
    return {
        TrendState.BULLISH.value: "UP",
        TrendState.BEARISH.value: "DOWN",
        TrendState.BULLISH_CAUTION.value: "CAUTION_UP",
        TrendState.BEARISH_CAUTION.value: "CAUTION_DOWN",
    }.get(trend_state, TrendState.RANGING.value)


def _unreachable(htf: str | None, pd: PdContext | None) -> tuple[str, ...]:
    names = list(UNREACHABLE_INPUTS)

    if htf is None:
        names.append(HTF_STATE_UNREACHABLE)

    # Suspended is a *reading*, not an absence: §5.7 looked and found the range
    # too narrow to divide. Only a range it could not build at all is unread.
    if pd is None:
        names.append(PD_CONTEXT_UNREACHABLE)

    return tuple(names)


def _state_direction(trend_state: str) -> str | None:
    """The direction a §3.7 trend state endorses, if any.

    CAUTION states are *not* endorsements: §3.7 enters them when a CHoCH has
    printed against the trend, which is the moment the trend is in question.
    They still reach G2 through the reversal branch when an MSS confirms.
    """
    if trend_state == TrendState.BULLISH.value:
        return "UP"

    if trend_state == TrendState.BEARISH.value:
        return "DOWN"

    return None


# §8.3.1's clean-break window: "no failed break against D in 20 candles".
CLEAN_RECORD_WINDOW = 20


def _count_failed_breaks(
    events: tuple[EngineEventRecord, ...],
    direction: str,
    opened_at: datetime,
    timeframe: Timeframe,
) -> int:
    """§8.3.1: failed breaks against D inside the clean-record window.

    Against D means a break *in* D that failed -- an UP setup is undermined by
    the market rejecting an upward break, not by a downward one failing. The
    window is measured to the candle that closed back through the level rather
    than to the break it belongs to: it asks how recently the market last
    refused this direction.

    That candle used to be identified by the payload's `failed_index`, checked
    as `last_index - failed_index < CLEAN_RECORD_WINDOW`. Those are offsets in
    two different 500-candle windows -- the one that recorded the failure, and
    the one being scored now -- so the subtraction did not measure elapsed
    candles. A failure recorded at the window's right edge, which is where
    they are recorded, keeps a difference near zero however long ago it
    happened, and so stays inside the clean-record window permanently.

    Unlike the sibling defects fixed alongside this, there is **no
    measurement** behind it: the soak build predates §3.5's detector and the
    VM holds zero `STRUCTURE_FAILED_BREAK_*` events, so nothing can be counted
    yet. The reasoning is the code's shape, not observed data, and it should be
    re-checked on real events once the detector is deployed.

    `event_at` is the failure candle's open time -- `structure_replay` stamps
    it from the same candle it takes `failed_index` from -- so the elapsed
    count is available without an offset.
    """
    failed = 0
    horizon = timeframe.duration * CLEAN_RECORD_WINDOW

    for record in events:
        if record.event_type != f"STRUCTURE_FAILED_BREAK_{direction}":
            continue

        if opened_at - record.event_at < horizon:
            failed += 1

    return failed


def _volume_score(
    reading: _Reading,
    direction: str,
    structural: bool,
    wash_risk: bool,
) -> Decimal:
    """F4 — §8.3.1: "Volume Factor Score as published" (§6.7).

    It was published as the RVOL *ratio*. `volume_factor` takes a 0-100 score
    and passes it through unmodified, and the call site handed it
    `reading.rvol` — a number between 0 and about 6 on real data, reaching 5
    only on the abnormal candles §6.4 exists to distrust. F4 was contributing
    about one point in a hundred where the design allots fifteen percent of the
    confidence, and §6.4's "capped at neutral (50)" could never bind because
    the score could not reach 50 to begin with. §6.7's own function was written
    and never called; this calls it.

    Read at the newest candle, not scanned across the window. §6.7 emits the
    score "per symbol-TF-candle", and asking the event log whether a spike
    happened anywhere in five hundred candles answers a different question
    whose answer is nearly always yes — in both directions at once, which
    would award +15 and -20 to every candidate alike.
    """
    participation = classify_participation(
        rvol=reading.rvol,
        delta=reading.delta,
        p90=reading.p90,
        median_p90=reading.median_p90,
        # §6.5(4): "institutional volume *at random locations* is not evidence
        # in this doctrine".
        structural=structural,
        suspect=reading.suspect,
    )

    return volume_factor(
        volume_factor_score(
            VolumeFactorEvidence(
                spike_aligned=reading.spike_direction == direction,
                opposing_spike=reading.spike_direction == ("DOWN" if direction == "UP" else "UP"),
                # §6.3's progress test already fixes the direction; #71 read
                # the detector's discarded sign as an absence of one.
                expansion_aligned=reading.expansion_direction == direction,
                # No direction needed. §6.3 calls contraction a warning of
                # exhaustion, and §6.7 charges it "against an active-move
                # claim" -- every directional candidate is such a claim, so
                # the contraction alone is the contrary evidence.
                contraction_against_claim=reading.contracting,
                institutional_volume=participation is ParticipationClass.INSTITUTIONAL,
                stealth_flow=participation is ParticipationClass.STEALTH,
                # §6.7's cap fires on either: §6.4's candle tag or §6.6's symbol one.
                integrity_suspect=reading.suspect or wash_risk,
            )
        ).score
    ).score


def _read_labels(events: tuple[EngineEventRecord, ...]) -> tuple[StructureLabel, ...]:
    """§3.3's swing labels in candle order, for §7.4's pair count.

    Read rather than recomputed, unlike the momentum and PD readings beside
    it: a label is assigned against the *previous same-kind swing*, so it is a
    fact about a series the structure engine walked, not a pure function of
    the newest candle.

    **STRUCTURE_EXTERNAL_*, not SWING_*.** The first version of this read
    SWING_* and would have returned nothing on every real context, leaving
    trend maturity pinned at zero exactly as the hardcoded value had --
    `_persist_swing` writes index/price/kind/strength and no label; the label
    only exists on the STRUCTURE_ event `_persist_classified_swing` writes.
    A fixture that supplies a label on a SWING_ event passes either way, which
    is why this was caught by reading a payload off the VM rather than by a
    test.

    External only. §7.4 counts the trend's pairs, and §3.4's trend state
    machine is driven by external swings; interleaving the internal series
    would let a k=2 LH inside an intact external uptrend read as the trend
    breaking.
    """
    labelled: list[tuple[int, StructureLabel]] = []

    for record in events:
        label = read_classification(record.event_type, strength=SwingStrength.EXTERNAL)

        # None covers both "not a classification" and "an internal one", and
        # also a label this build does not recognise -- version skew, not a
        # reason to stop scoring. §7.4 counts what it recognises.
        if label is None:
            continue

        index = json.loads(record.payload).get("index")

        if index is None:
            continue

        labelled.append((int(index), label))

    labelled.sort()

    return tuple(label for _, label in labelled)


def _size_skew(
    series: Sequence[Candle],
    minutes: Sequence[TradeAggregate],
    timeframe: Timeframe | None,
) -> tuple[Decimal | None, Decimal | None]:
    """§6.5's two size numbers: this candle's p90, and the trailing median.

    Both from the same estimator, which is the only property the comparison
    needs -- `candle_p90` explains why an estimator is all there can be.

    `(None, None)` whenever the tape is not covered. §6.5's validation asks for
    "aggTrade data fresh", and a candle whose minutes are missing has not been
    found to lack big prints; it has not been looked at.
    """
    if timeframe is None or not minutes or not series:
        return None, None

    buckets: dict[datetime, list[TradeAggregate]] = {}

    step = timeframe.duration
    origin = series[-1].open_time

    for item in minutes:
        # Which candle this minute fell in, counted back from the newest so a
        # partial leading candle cannot shift every bucket by one.
        offset = (origin - item.minute) // step
        buckets.setdefault(origin - step * offset, []).append(item)

    current = candle_p90(buckets.get(origin, []))

    trailing = [
        value
        for index in range(1, INSTITUTIONAL_MEDIAN_WINDOW + 1)
        if (value := candle_p90(buckets.get(origin - step * index, []))) is not None
    ]

    return current, median(trailing) if trailing else None


def _read_participation(
    series: list[Candle],
    minutes: Sequence[TradeAggregate] = (),
    timeframe: Timeframe | None = None,
) -> _Reading:
    """§6 and §7 at the newest candle, recomputed rather than read back.

    Scanning the event log for the last VOLUME_SPIKE or MOMENTUM_ACCELERATING
    answered a different question -- "did this ever happen in the window" -- and
    over 1849 candles the answer is always yes. Correcting §7.2's exhaustion
    rule cut the tag from 479 candles to 32 and moved confluence's confidence
    not at all, because both counts are non-zero somewhere in two months.

    Recomputing is safe *here* and not for trend state, and the difference is
    the point. §7.1 calls the momentum score a "per-candle measurement (fact)"
    and §6.2's RVOL likewise: both are pure functions of closed candles, so a
    recomputation cannot disagree with the engine that owns them. Trend state is
    a state machine with history, which is why it arrives as an argument.

    The continuous series is also not in the event log by design -- §6 and §7
    persist only the candles where the reading is a *fact*, since a class on
    every bar is a series, not an event. So there is nothing to read back.
    """
    last = len(series) - 1

    score = momentum_score(series, last)
    phase = momentum_phase(series, last)
    p90, median_p90 = _size_skew(series, minutes, timeframe)

    spike = detect_volume_spike(series, last)
    expansion = detect_expansion(series, last)
    check = cross_validate_abnormal_volume(series, last)

    return _Reading(
        rvol=relative_volume(series, last),
        score=score.score if score else Decimal(0),
        direction=score.direction.value if score else None,
        accelerating=phase.accelerating if phase else False,
        # §8.3.1 pays "accelerating 25 - neither 12 - decelerating 0".
        # `momentum_factor` reads this and nothing set it, so a fading
        # trend was paid the 12 that belongs to a steady one.
        decelerating=phase.decelerating if phase else False,
        exhausted=phase.exhaustion_watch if phase else False,
        # §6.2 and §6.4 at this candle, recomputed like the momentum reading
        # beside them: both are pure functions of closed candles, so a
        # recomputation cannot disagree with the engine that owns them.
        spike_direction=spike.direction if spike else None,
        expansion_direction=expansion.direction if expansion else None,
        contracting=detect_contraction(series, last),
        # The depth half of §6.4 is unread here for the same reason it is
        # unread in the engine -- `market.liquidity_history` is empty -- so
        # both reach the same verdict from the same evidence.
        suspect=check.suspect if check else False,
        delta=delta_pct(series[last]),
        p90=p90,
        median_p90=median_p90,
    )


# §5 defines zone lifecycle twice: OB, breaker and OTE zones carry ZoneState
# (FRESH/TESTED/...), FVG-family zones carry FvgState (OPEN/TOUCHED/CE_FILLED),
# because only the FVG has a consequential midpoint. §8.3.1's state table names
# the first vocabulary.
#
# Without this bridge every FVG, IFVG and BPR zone scores zero state points --
# not because it is stale, but because "OPEN" is not a key in the table. The two
# names describe the same two facts: untouched, and touched.
_FVG_STATE_EQUIVALENT: dict[str, str] = {
    "OPEN": "FRESH",
    "TOUCHED": "TESTED",
}


def _zone_score(
    zone: IctZoneRecord,
    stack_depth: int,
    *,
    entry_confirmation: bool = False,
) -> Decimal:
    """F3 for one zone — §8.3.1.

    The ranking key that picks `best_zone` leaves `entry_confirmation` at its
    default: reading every candidate zone's interaction history to choose
    between them would be a query per zone, and the term is worth 10 of 100 —
    not enough to reorder a stack, and the chosen zone is scored with it.
    """
    return zone_factor(
        ZoneEvidence(
            grade=zone.grade,
            state=_FVG_STATE_EQUIVALENT.get(zone.state, zone.state),
            stack_depth=stack_depth,
            entry_confirmation=entry_confirmation,
        )
    ).score


def _synergies(
    event_types: set[str],
    zones: list[IctZoneRecord],
) -> list[Adjustment]:
    """§8.5 bonuses that are reachable from what the engines record today."""
    bonuses: list[Adjustment] = []

    if len(zones) >= 2:
        bonuses.append(Adjustment("zone_stack", Decimal(5)))

    if "COMPRESSION" in event_types:
        bonuses.append(Adjustment("compression_resolved", Decimal(4)))

    return bonuses
