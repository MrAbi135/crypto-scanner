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

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.detection.state import EngineStateManager
from scanner.application.marketdata.contexts import higher_timeframe
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
)
from scanner.application.ports.ict_zones import IctZoneRecord, IctZoneRepository
from scanner.domain.common import Candle
from scanner.domain.common.rvol import relative_volume
from scanner.domain.confluence import (
    Adjustment,
    ArchetypeEvidence,
    Factor,
    GateEvidence,
    LiquidityEvidence,
    MomentumEvidence,
    StructureEvidence,
    ZoneEvidence,
    classify_archetype,
    evaluate_gates,
    final_confidence,
    htf_alignment_factor,
    liquidity_factor,
    meets_floor,
    momentum_factor,
    structure_factor,
    volume_factor,
    zone_factor,
)
from scanner.domain.momentum import momentum_phase, momentum_score
from scanner.domain.structure import TrendState
from scanner.shared import Timeframe

CONFLUENCE_ALGO_VERSION = "s8-confluence-v3"

# Inputs §8 asks for that no engine currently produces. Listed rather than
# silently defaulted -- see the module docstring.
UNREACHABLE_INPUTS: tuple[str, ...] = (
    "wash_risk",  # §6.6 needs aggTrade aggregates (roadmap S2 T4, never built)
    "pd_context",  # §5.7 directional gate has no persisted output
    "entry_confirmation",  # §5.9 Confirmations need the LTF ladder populated
    # §4.2's strength model exists, but nothing selects which pool a setup is
    # *targeting*, so the three F2 terms that describe that pool are all unread.
    "target_pool_strength",
    "target_pool_unclaimed",
    "target_pool_fresh",
    # §8.6 archetype chains all terminate in a retest tied to an entry
    # price. Without one, no chain closes and no candidate can classify.
    "archetype_retest_chain",
)

# Not in the constant above, because unlike those it is only *sometimes*
# unreachable: §8.4 reads the timeframe one rung up, which may be the top of
# the ladder or may simply never have been replayed. Reporting it always would
# be as misleading as reporting it never.
HTF_STATE_UNREACHABLE = "htf_state"


@dataclass(frozen=True, slots=True)
class _Reading:
    """§6 and §7 at the context's newest candle."""

    rvol: Decimal | None
    score: Decimal
    direction: str | None
    accelerating: bool
    exhausted: bool


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
        clock: Clock,
        shift_state: EngineStateManager,
        *,
        shift_algo_version: str,
        algo_version: str = CONFLUENCE_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._events = events
        self._zones = zones
        self._evidence = evidence
        self._clock = clock
        self._shift_state = shift_state
        self._shift_algo_version = shift_algo_version
        self._algo_version = algo_version

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

        event_types = {record.event_type for record in events}

        reading = _read_participation(series)

        htf = await self._read_htf_state(symbol, timeframe)

        candidates: list[SetupCandidate] = []
        inserted = 0

        for direction in ("UP", "DOWN"):
            candidate = self._evaluate(
                symbol=symbol,
                timeframe=timeframe,
                last_index=len(series) - 1,
                direction=direction,
                trend_state=trend_state,
                event_types=event_types,
                liquidity=liquidity,
                live_zones=live_zones,
                reading=reading,
                htf=htf,
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
        )

    def _evaluate(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        last_index: int,
        direction: str,
        trend_state: str,
        event_types: set[str],
        liquidity: tuple[LiquidityEvidenceRecord, ...],
        live_zones: tuple[IctZoneRecord, ...],
        reading: _Reading,
        htf: str | None,
    ) -> SetupCandidate:
        polarity = "BULLISH" if direction == "UP" else "BEARISH"

        matching_zones = [z for z in live_zones if z.polarity == polarity]

        sweeps = [_Sweep(r) for r in liquidity if r.reason == "liquidity_sweep"]

        # §4.6: "a sweep's setup relevance expires P.liquidity.sweep_expiry = 15
        # closed candles after confirmation -- beyond that window it may no
        # longer seed MSS or Sweep-Reversal archetypes". The liquidity engine
        # already stamped the expiry index; nothing was reading it.
        live_sweeps = [s for s in sweeps if s.live(last_index)]

        supporting = [s for s in live_sweeps if s.supports(direction) and not s.reclaimed]

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
                # §5.7 has no persisted output, so this passes rather than
                # blocking every candidate on an unbuilt gate. Recorded in
                # `unreachable` so the pass is not mistaken for a check.
                pd_context_ok=True,
                zone_present=bool(matching_zones),
                contrary_fact_present=bool(contrary),
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
                unreachable=_unreachable(htf),
            )

        # The best zone in the stack, not the newest one. §8.3 F3 asks for the
        # quality of the zone the setup sits at, and with no entry price to
        # locate price inside the stack, the strongest member is the only
        # defensible reading. Recency breaks ties.
        best_zone = max(
            matching_zones,
            key=lambda z: (_zone_score(z, len(matching_zones)), z.confirmed_index),
        )

        factors = {
            Factor.STRUCTURE: structure_factor(
                StructureEvidence(
                    break_confirmed=f"BOS_{direction}" in event_types,
                    displaced=f"MSS_{direction}" in event_types,
                    external=any(t.startswith("STRUCTURE_EXTERNAL") for t in event_types),
                    mss=f"MSS_{direction}" in event_types,
                    unbroken_pairs=0,
                    failed_breaks=0,
                )
            ).score,
            Factor.LIQUIDITY: liquidity_factor(
                LiquidityEvidence(
                    sweep_confirmed=bool(supporting),
                    external=any(s.external for s in supporting),
                    depth_atr=max((s.depth_atr for s in supporting), default=Decimal(0)),
                    # Not defaulted True: an unread term must not pay points.
                    # Both describe the target pool, which nothing selects yet.
                    unclaimed=False,
                    fresh=False,
                    stop_hunt="LIQUIDITY_STOP_HUNT" in event_types,
                    target_pool_strength=Decimal(0),
                )
            ).score,
            Factor.ZONE: _zone_score(best_zone, len(matching_zones)),
            Factor.VOLUME: volume_factor(reading.rvol or Decimal(0)).score,
            Factor.MOMENTUM: momentum_factor(
                MomentumEvidence(
                    score=reading.score,
                    aligned=reading.direction == direction,
                    accelerating=reading.accelerating,
                    exhaustion_against=reading.exhausted,
                )
            ).score,
            Factor.HTF_ALIGNMENT: htf_alignment_factor(
                # RANGING when the rung above has no state, and said so in
                # `unreachable`. It is F6's neutral value, so an unread ladder
                # neither rewards nor punishes a candidate -- but a reader can
                # tell that from the record instead of having to guess.
                htf_state=htf if htf is not None else TrendState.RANGING.value,
                direction=direction,
            ).score,
        }

        confidence = final_confidence(factors, _synergies(event_types, matching_zones))

        # §8.6's five chains each end in a *retest*: "retest of MSS-origin
        # zone", "first retest with Respect", "retrace into OTE/OB/FVG", "first
        # touch". Every one of those links a specific zone to a specific entry,
        # and this service has no entry price to anchor them with -- it grades a
        # context, not a trade. So no archetype can currently classify, and the
        # chain terms below are the ones that *are* readable rather than a
        # plausible-looking set that happens to never match.
        archetype = classify_archetype(
            ArchetypeEvidence(
                external_sweep=any(s.external for s in supporting),
                mss_confirmed=trend_following and f"MSS_{direction}" in event_types,
                stop_hunt_confirmed="LIQUIDITY_STOP_HUNT" in event_types,
                breaker_formed=any(z.grade == "BRK_A" for z in matching_zones),
                trend_active=trend_following,
                displaced_bos=f"MSS_{direction}" in event_types,
                retraced_into_zone=bool(matching_zones),
                htf_aligned=htf == direction,
            )
        )

        publishable = (
            archetype is not None
            and confidence.published_grade is not None
            and meets_floor(archetype, confidence.final)
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
            zone_id=best_zone.zone_id,
            unreachable=_unreachable(htf),
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
                        "factors": {k: str(v) for k, v in candidate.factors.items()},
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

        return 1 if inserted else 0

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

    __slots__ = ("_e", "confirmed_index")

    def __init__(self, record: LiquidityEvidenceRecord) -> None:
        self._e = json.loads(record.evidence)
        self.confirmed_index = record.candle_index

    @property
    def reclaimed(self) -> bool:
        return bool(self._e.get("reclaimed"))

    @property
    def external(self) -> bool:
        return bool(self._e.get("liquidity_class") == "EXTERNAL")

    @property
    def depth_atr(self) -> Decimal:
        raw = self._e.get("sweep_depth_atr")

        return Decimal(str(raw)) if raw is not None else Decimal(0)

    def live(self, last_index: int) -> bool:
        expiry = self._e.get("setup_expiry_index")

        return expiry is None or last_index <= int(expiry)

    def supports(self, direction: str) -> bool:
        """A sweep of resting sell-side liquidity clears the way up, and vice versa."""
        side = self._e.get("side")

        return bool(side == "SSL") if direction == "UP" else bool(side == "BSL")


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


def _unreachable(htf: str | None) -> tuple[str, ...]:
    if htf is None:
        return (*UNREACHABLE_INPUTS, HTF_STATE_UNREACHABLE)

    return UNREACHABLE_INPUTS


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


def _read_participation(series: list[Candle]) -> _Reading:
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

    return _Reading(
        rvol=relative_volume(series, last),
        score=score.score if score else Decimal(0),
        direction=score.direction.value if score else None,
        accelerating=phase.accelerating if phase else False,
        exhausted=phase.exhaustion_watch if phase else False,
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


def _zone_score(zone: IctZoneRecord, stack_depth: int) -> Decimal:
    """F3 for one zone — §8.3.1."""
    return zone_factor(
        ZoneEvidence(
            grade=zone.grade,
            state=_FVG_STATE_EQUIVALENT.get(zone.state, zone.state),
            stack_depth=stack_depth,
            # §5.9 Confirmations need the LTF ladder populated; see UNREACHABLE_INPUTS.
            entry_confirmation=False,
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
