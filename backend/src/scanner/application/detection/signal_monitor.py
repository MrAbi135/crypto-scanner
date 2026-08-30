"""§12.3's monitoring: advance every live signal on one closed candle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import EngineEventRepository
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionContextRepository,
)
from scanner.application.ports.setups import SetupRecord, SetupRepository
from scanner.application.ports.signal_outcomes import (
    SignalOutcomeRecord,
    SignalOutcomeRepository,
)
from scanner.application.ports.signal_transitions import (
    SignalTransitionRecord,
    SignalTransitionRepository,
)
from scanner.application.ports.signals import SignalRecord, SignalRepository
from scanner.domain.confluence import (
    SignalLevels,
    TargetBand,
    entry_zone,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import Candle as LifecycleCandle
from scanner.domain.lifecycle import SignalState, accounting, observe
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class MonitorReport:
    symbol: str
    timeframe: Timeframe
    at: datetime
    live_before: int
    transitions: int
    stress_tests: int
    resolved: int


class SignalMonitorService:
    """§12.3, once per closed candle, for one symbol-timeframe.

    Reads the levels back out of T17's sealed payload rather than
    recomputing them. §12.1 is explicit that "evidence, zones, levels never
    mutate post-creation", so the levels a signal is monitored against must be
    the ones it was published with — recomputing would quietly re-aim a live
    signal every time the market moved a zone underneath it.
    """

    def __init__(
        self,
        candles: CandleRepository,
        signals: SignalRepository,
        transitions: SignalTransitionRepository,
        clock: Clock,
        outcomes: SignalOutcomeRepository | None = None,
        *,
        zone_context: IctZoneInteractionContextRepository | None = None,
        evidence: IctEvidenceRepository | None = None,
        setups: SetupRepository | None = None,
        events: EngineEventRepository | None = None,
    ) -> None:
        self._candles = candles
        self._signals = signals
        self._transitions = transitions
        self._clock = clock
        self._outcomes = outcomes
        self._zone_context = zone_context
        self._evidence = evidence
        self._setups = setups
        self._events = events

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        at: datetime,
    ) -> MonitorReport:
        live_ids = await self._transitions.list_live(symbol, timeframe.value)

        series = await self._candles.fetch_series(
            symbol,
            timeframe,
            at,
            at + timeframe.duration,
        )

        if not series:
            return MonitorReport(symbol, timeframe, at, len(live_ids), 0, 0, 0)

        closed = series[-1]

        candle = LifecycleCandle(
            high=closed.high,
            low=closed.low,
            close=closed.close,
        )

        transitions = 0
        stress = 0
        resolved = 0

        for signal_id in live_ids:
            signal = await self._signals.get(signal_id)

            if signal is None:
                continue

            state = await self._transitions.current_state(signal_id)

            if state is None:
                continue

            source = SignalState(state)

            premise = (
                await self._broken_premise(signal, timeframe, at)
                if source is SignalState.PUBLISHED
                else None
            )

            observation = observe(
                source,
                candle,
                levels=_levels_of(signal),
                elapsed_candles=_elapsed(signal, at, timeframe),
                ttl_candles=signal.ttl_candles,
                premise_broken=premise is not None,
            )

            if observation.to_state is None and not observation.stress_test:
                continue

            target = observation.to_state or source

            written = await self._transitions.append(
                SignalTransitionRecord(
                    transition_id=_transition_id(signal_id, at),
                    signal_id=signal_id,
                    from_state=source.value,
                    to_state=target.value,
                    at_candle_open_time=at,
                    recorded_at=self._clock.now(),
                    stress_test=observation.stress_test,
                    refresh=False,
                    trigger_evidence=json.dumps(
                        {
                            "reason": observation.reason,
                            "high": str(closed.high),
                            "low": str(closed.low),
                            "close": str(closed.close),
                            **({"premise": premise} if premise is not None else {}),
                        },
                        sort_keys=True,
                    ),
                )
            )

            if not written:
                continue

            if observation.stress_test:
                stress += 1

            if observation.to_state is not None:
                transitions += 1

                if observation.to_state in _RESOLVED:
                    resolved += 1

                    await self._record_outcome(
                        signal,
                        observation.to_state,
                        at=at,
                        timeframe=timeframe,
                        reason=observation.reason,
                    )

        return MonitorReport(
            symbol=symbol,
            timeframe=timeframe,
            at=at,
            live_before=len(live_ids),
            transitions=transitions,
            stress_tests=stress,
            resolved=resolved,
        )

    async def _broken_premise(
        self,
        signal: SignalRecord,
        timeframe: Timeframe,
        at: datetime,
    ) -> str | None:
        """§12.3's premise checks, pre-touch only: the reason, or None.

        All three of the doctrine's premises, each with exact linkage:

        * **zone violated** -- the entry zone's own transition rows;
        * **MSS demoted** -- the STRUCTURE_MSS_INVALIDATED_{dir} events the
          shift engine publishes when §3.6's reclaim demotes an MSS;
        * **sweep reclaimed** -- the seeding sweep's pool, read from the
          setup's own F2 attribution (signal and setup share one id), matched
          against LIQUIDITY_SWEEP_RECLAIMED events. A setup recorded before
          the attribution carried ids yields no pool and the check stays
          quiet -- honest for old rows, exact for new ones.

        Every check bounds the fact to **strictly before** the candle being
        observed (`<= at`, the candle's open). A premise that broke on the
        same candle the entry was touched is an unknowable order, and
        INVALIDATED_EARLY takes the signal out of the accounting -- awarding
        the exclusion on the favourable reading of an unknowable order is
        what §15.4 forbids. The fact is not lost: if the entry stays
        untouched, the next candle's pass reads it.
        """
        if self._zone_context is not None:
            zone_id = _entry_zone_id(signal)

            if zone_id is not None:
                for transition in await self._zone_context.list_transitions(zone_id):
                    if (
                        transition.to_state == "INVALIDATED"
                        and signal.published_at < transition.transitioned_at <= at
                    ):
                        return "entry_zone_invalidated"

        if self._evidence is not None:
            wanted = f"STRUCTURE_MSS_INVALIDATED_{signal.direction}"

            # The strictly-before bound is the query's exclusive end:
            # event_at is always a close time on the TF grid, so everything
            # `< at + duration` closed at or before `at` -- no second,
            # in-code copy of the bound exists to drift from this one.
            records = await self._evidence.list_structure(
                signal.symbol,
                timeframe,
                signal.published_at,
                at + timeframe.duration,
            )

            for record in records:
                if record.event_type == wanted:
                    return "mss_demoted_to_ranging"

        if self._setups is not None and self._events is not None:
            setup = await self._setups.get(signal.setup_id)
            pool_id = _seeding_sweep_pool(setup)

            if pool_id is not None:
                # Same strictly-before bound as the MSS check: list_events'
                # end is exclusive and event_at sits on the TF grid.
                events = await self._events.list_events(
                    signal.symbol,
                    timeframe,
                    signal.published_at,
                    at + timeframe.duration,
                )

                for event in events:
                    if event.event_type != "LIQUIDITY_SWEEP_RECLAIMED":
                        continue

                    try:
                        reclaimed_pool = json.loads(event.payload).get("pool_id")
                    except ValueError:
                        continue

                    if reclaimed_pool == pool_id:
                        return "seeding_sweep_reclaimed"

        return None

    async def _record_outcome(
        self,
        signal: SignalRecord,
        outcome: SignalState,
        *,
        at: datetime,
        timeframe: Timeframe,
        reason: str,
    ) -> None:
        """§12.4's accounting, written once when the signal resolves.

        The excursions are computed from the candles the signal actually lived
        through, fetched here rather than accumulated as it ran. An
        accumulator would need updating on tables with no UPDATE surface, and
        a monitor that missed a candle would under-report the excursion for
        the rest of the signal's life -- silently, and in the direction that
        flatters the record.
        """
        if self._outcomes is None:
            return

        lived = await self._candles.fetch_series(
            signal.symbol,
            timeframe,
            signal.published_at,
            at + timeframe.duration,
        )

        levels = _levels_of(signal)

        try:
            book = accounting(
                outcome,
                levels=levels,
                candles=[LifecycleCandle(high=c.high, low=c.low, close=c.close) for c in lived],
            )
        except ValueError:
            # R is zero, which T17's own check constraint should have refused
            # at publication. Recording no outcome is better than recording an
            # infinite one, and the signal's transition history still says
            # what happened to it.
            return

        await self._outcomes.append(
            SignalOutcomeRecord(
                signal_id=signal.signal_id,
                outcome=outcome.value,
                resolved_at=at,
                elapsed_candles=book.elapsed_candles,
                mfe_r=book.mfe_r,
                mae_r=book.mae_r,
                excluded_from_stats=False,
                resolution_evidence=json.dumps({"reason": reason}, sort_keys=True),
            )
        )


_RESOLVED = frozenset(
    {
        SignalState.SUCCESS,
        SignalState.FAILED,
        SignalState.EXPIRED_ACTIVE,
        SignalState.EXPIRED_UNTOUCHED,
        SignalState.INVALIDATED_EARLY,
    }
)


def _seeding_sweep_pool(setup: SetupRecord | None) -> str | None:
    """The pool behind the setup's F2 `sweep_confirmed` award, if it cited one.

    That contribution's evidence id IS the seeding sweep's pool -- the same
    id the maturation events carry -- so the reclaim premise needs no second
    bookkeeping of which sweep a signal stood on.
    """
    if setup is None:
        return None

    try:
        contributions = json.loads(setup.evidence)["attribution"]["F2"]
    except (KeyError, TypeError, ValueError):
        return None

    for item in contributions:
        if isinstance(item, dict) and item.get("code") == "sweep_confirmed":
            pool_id = item.get("evidence_id")

            # isinstance narrows the JSON Any; it is not a validity check.
            # Pre-v26 rows carry null here and correctly yield None.
            return pool_id if isinstance(pool_id, str) else None

    return None


def _entry_zone_id(signal: SignalRecord) -> str | None:
    """The entry zone's id, from the sealed §15.2 payload.

    The priced columns beside the payload deliberately do not carry it --
    they exist so the per-candle level checks never parse JSON -- and the
    premise check runs only while the signal is PUBLISHED, which is at most
    a handful of signals per pass.
    """
    try:
        payload = json.loads(signal.payload)
        zone_id = payload["entry_zone"]["zone_id"]
    except (KeyError, TypeError, ValueError):
        return None

    if not isinstance(zone_id, str) or zone_id == "":
        return None

    return zone_id


def _elapsed(signal: SignalRecord, at: datetime, timeframe: Timeframe) -> int:
    """Closed candles since publication, for §12.5's TTL.

    Counted from the timestamps rather than from a stored counter: a counter
    would have to be updated on a table that has no UPDATE surface, and a
    monitor that missed a candle would then under-count forever.
    """
    return int((at - signal.published_at) // timeframe.duration)


def _levels_of(signal: SignalRecord) -> SignalLevels:
    """The levels this signal was published with, from its own columns.

    T17 extracts the priced rows beside the sealed payload precisely so a
    monitor does not have to parse JSON on every candle. The targets do come
    from the payload column, because a signal can carry two of them and only
    the primary is extracted.
    """
    targets = json.loads(signal.target_bands)
    primary = targets["primary"]

    return SignalLevels(
        direction=signal.direction,
        entry=entry_zone(
            zone_id="",
            direction=signal.direction,
            band_low=min(signal.entry_proximal, signal.entry_distal),
            band_high=max(signal.entry_proximal, signal.entry_distal),
        ),
        invalidation=Invalidation(signal.invalidation_level, ""),
        primary_target=TargetBand(
            low=Decimal(primary["low"]),
            high=Decimal(primary["high"]),
            pool_id=primary.get("pool_id"),
        ),
    )


def _transition_id(signal_id: str, at: datetime, *, refresh: bool = False) -> str:
    """One transition per signal per candle, and the id says so.

    Derived from the natural key the table is unique on, so a replayed candle
    collides on both and the second write is a no-op rather than a duplicate
    history entry.

    `refresh` is part of that key. §12's monitor and §10.3's merge both write
    on the same closed candle, so without it a signal that moved *and* was
    re-detected on one candle would produce two rows with one id -- and the
    second, whichever it was, would be silently dropped.
    """
    raw = "|".join((signal_id, at.isoformat(), "refresh" if refresh else "transition"))

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
