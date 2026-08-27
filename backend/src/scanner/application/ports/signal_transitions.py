"""Application port for T18 `detection.signal_transitions` (DDD T18, SLS §12)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SignalTransitionRecord:
    """One §12 transition, or one `stress_test` observation.

    §12.3 records a wick through the invalidation as a fact about the candle
    without moving the signal, so those rows carry `from_state == to_state`.
    A nullable `to_state` would make every reader handle a null before it
    could ask the only question that matters.
    """

    transition_id: str
    signal_id: str
    from_state: str
    to_state: str
    at_candle_open_time: datetime
    recorded_at: datetime
    stress_test: bool

    # §10.3's merge: a second candidate on a live key appends its evidence
    # here instead of publishing. Also `from_state == to_state` -- a
    # refresh is news about the signal, not a move.
    refresh: bool
    trigger_evidence: str


class SignalTransitionRepository(Protocol):
    async def append(self, transition: SignalTransitionRecord) -> bool:
        """Record one transition. False when this candle was already read.

        Append-only, and unique on (signal, candle): a second reading of the
        same close is a replay, not a new fact. §12 monitors "per closed
        candle", which means one candle gets one verdict.
        """
        ...

    async def current_state(self, signal_id: str) -> str | None:
        """The signal's state now: the latest transition's `to_state`.

        None when nothing has been recorded, which cannot happen for a
        published signal -- §12.2's own PUBLISHED transition is written with
        it. A reader that sees None is looking at a signal that was never
        published or a write that did not land, and both deserve to surface
        rather than default to DETECTED.
        """
        ...

    async def list_for_signal(self, signal_id: str) -> tuple[SignalTransitionRecord, ...]:
        """§18.8's lifecycle row: one signal's history, oldest first.

        Refresh rows included. §12.3's `stress_test` observations are in the
        spec's own description of this row ("incl. stress_test events"), and
        §10.3's refreshes are the same kind of fact — a reader asking what
        happened to a signal wants both. The two state queries exclude them for
        a different reason (ordering, see the repository), which is why this is
        a separate method rather than a flag on those.
        """
        ...

    async def list_live(self, symbol: str, timeframe: str) -> tuple[str, ...]:
        """Signal ids on this series whose latest state is not terminal."""
        ...

    async def live_states(self) -> tuple[tuple[str, str], ...]:
        """Every live signal id with the state it currently holds.

        §18.4's feed is one board across every symbol and timeframe, so asking
        `list_live` per series would be a query per context and would still not
        say which state each signal is in -- and §18.4 renders that state.

        Returned as pairs rather than a mapping because the caller orders by
        §9.2 and a dict would invite ordering by insertion instead.
        """
        ...
