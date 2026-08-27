"""§18.4's live feed — every live signal, ordered by §9.2, decayed by §9.3.

The API spec calls this row "THE core read", and it is the one board that is
not scoped to a close: §18.6's rankings answer "what did this timeframe offer
at 12:00", while the feed answers "what is on the table right now", across
every symbol and every timeframe at once.

**Decay is per signal, not per board.** `RankingSnapshotService` takes one
`elapsed_candles` for the whole snapshot, which is right for it -- every row
there was recorded at the same close. Here the rows were published at
different times on timeframes of different lengths, so a single elapsed count
would decay an H4 signal published an hour ago exactly as hard as an M5 signal
published a day ago. §9.3 measures age in candles *of the signal's own
timeframe*, so each row carries its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import Clock
from scanner.application.ports.repositories import SymbolRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRecord, SignalRepository
from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence import Archetype
from scanner.domain.ranking import RankableSetup, display_rank, rank


@dataclass(frozen=True, slots=True)
class FeedRow:
    position: int
    signal: SignalRecord
    lifecycle_state: str

    # §9.3's decayed number. Kept beside the recorded confidence rather than
    # replacing it: §15.4 wants both visible, and a reader given only the
    # decayed figure cannot tell a weakening signal from a weak one.
    display: Decimal
    elapsed_candles: int


@dataclass(frozen=True, slots=True)
class Feed:
    generated_at: datetime
    rows: tuple[FeedRow, ...]

    # The denominator, for the reason §18.6's board carries one: a feed showing
    # only its rows makes a quiet market and a broken pipeline look identical.
    live_total: int


class LiveFeedService:
    """§18.4's `/scanner/feed`."""

    def __init__(
        self,
        signals: SignalRepository,
        transitions: SignalTransitionRepository,
        symbols: SymbolRepository,
        clock: Clock,
    ) -> None:
        self._signals = signals
        self._transitions = transitions
        self._symbols = symbols
        self._clock = clock

    async def read(self) -> Feed:
        now = self._clock.now()

        live = await self._transitions.live_states()

        records: list[tuple[SignalRecord, str]] = []

        for signal_id, state in live:
            record = await self._signals.get(signal_id)

            # T18 holds a transition for a signal T17 does not. §12.2 makes
            # that impossible, so it is skipped rather than rendered as a row
            # with no content -- but it is not silently equivalent to "no live
            # signals", which is why `live_total` counts what T18 said.
            if record is not None:
                records.append((record, state))

        tiers = {
            symbol: await self._tier(symbol) for symbol in {record.symbol for record, _ in records}
        }

        rankable = {
            record.signal_id: RankableSetup(
                symbol=record.symbol,
                timeframe=record.timeframe,
                confidence=record.final_confidence,
                archetype=Archetype(record.archetype),
                tier=tiers[record.symbol],
                direction=record.direction,
            )
            for record, _ in records
        }

        by_id = {record.signal_id: (record, state) for record, state in records}
        position_of = {setup: index for index, setup in enumerate(rank(rankable.values()), start=1)}

        rows = []

        for signal_id, setup in rankable.items():
            record, state = by_id[signal_id]
            elapsed = _elapsed_candles(record, now)

            rows.append(
                FeedRow(
                    position=position_of[setup],
                    signal=record,
                    lifecycle_state=state,
                    display=display_rank(
                        record.final_confidence,
                        elapsed_candles=elapsed,
                        timeframe=record.timeframe,
                    ),
                    elapsed_candles=elapsed,
                )
            )

        return Feed(
            generated_at=now,
            rows=tuple(sorted(rows, key=lambda row: row.position)),
            live_total=len(live),
        )

    async def _tier(self, symbol: str) -> UniverseTier:
        """The symbol's §1.4 tier, or INELIGIBLE when the registry has none.

        Not T3, for the reason `RankingSnapshotService` gives: an unknown tier
        is not a weak one, and §9.2's fourth key would otherwise rank an
        unregistered symbol above a genuinely tier-3 one.
        """
        state = await self._symbols.get_universe_state(symbol)

        return state.tier if state is not None else UniverseTier.INELIGIBLE


def _elapsed_candles(signal: SignalRecord, now: datetime) -> int:
    """Closed candles of the signal's own timeframe since it was published.

    §9.3 decays "per candle elapsed", and a candle of one timeframe is not a
    candle of another -- an H4 signal four hours old has aged one candle where
    an M5 signal four hours old has aged forty-eight. Floored at zero because a
    clock injected behind `published_at` is a test's business, not a negative
    age.
    """
    step = signal.timeframe.duration.total_seconds()

    if step <= 0:
        return 0

    return max(0, int((now - signal.published_at).total_seconds() // step))
