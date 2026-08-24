"""§12.3's monitoring: advance every live signal on one closed candle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import CandleRepository, Clock
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
    ) -> None:
        self._candles = candles
        self._signals = signals
        self._transitions = transitions
        self._clock = clock
        self._outcomes = outcomes

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

            observation = observe(
                source,
                candle,
                levels=_levels_of(signal),
                elapsed_candles=_elapsed(signal, at, timeframe),
                ttl_candles=signal.ttl_candles,
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
                    trigger_evidence=json.dumps(
                        {
                            "reason": observation.reason,
                            "high": str(closed.high),
                            "low": str(closed.low),
                            "close": str(closed.close),
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


def _transition_id(signal_id: str, at: datetime) -> str:
    """One transition per signal per candle, and the id says so.

    Derived from the natural key the table is unique on, so a replayed candle
    collides on both and the second write is a no-op rather than a duplicate
    history entry.
    """
    raw = "|".join((signal_id, at.isoformat()))

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
