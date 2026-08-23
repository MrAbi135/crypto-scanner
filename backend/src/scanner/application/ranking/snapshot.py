"""Read the setups recorded at one close and order them (SLS §9.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports.repositories import SymbolRepository
from scanner.application.ports.setups import SetupRecord, SetupRepository
from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence import Archetype
from scanner.domain.ranking import RankableSetup, display_rank, rank
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class RankedRow:
    """One board row: where §9.2 put it, and what §9.3 shows for it."""

    position: int
    setup: RankableSetup
    display: Decimal


@dataclass(frozen=True, slots=True)
class RankingSnapshot:
    timeframe: Timeframe
    at: datetime
    rows: tuple[RankedRow, ...]

    # Everything T16 holds for this close, and how much of it stopped at the
    # floor. §8.6 keeps the below-floor candidates "for calibration", and a
    # board reporting only its rows would make a quiet market and a broken
    # pipeline look identical.
    #
    # These count *gate-passing* candidates, because that is T16's population
    # -- a gate failure is in the event log and nowhere else. The earlier
    # version of this service read the event log and so counted those too;
    # the number is smaller now and means something sharper.
    gate_passers: int
    below_floor: int


class RankingSnapshotService:
    """§9.2 over T16's rows at a single close."""

    def __init__(
        self,
        setups: SetupRepository,
        symbols: SymbolRepository,
    ) -> None:
        self._setups = setups
        self._symbols = symbols

    async def snapshot(
        self,
        symbols: tuple[str, ...],
        timeframe: Timeframe,
        at: datetime,
        *,
        elapsed_candles: int = 0,
    ) -> RankingSnapshot:
        rows = await self._setups.list_at(symbols, timeframe, at)

        published = [row for row in rows if row.floor_passed]

        tiers = {symbol: await self._tier(symbol) for symbol in {r.symbol for r in published}}

        ordered = rank(
            RankableSetup(
                symbol=row.symbol,
                timeframe=row.timeframe,
                confidence=row.final_confidence,
                archetype=_archetype_of(row),
                tier=tiers[row.symbol],
                direction=row.direction,
            )
            for row in published
        )

        board = tuple(
            RankedRow(
                position=position,
                setup=setup,
                display=display_rank(
                    setup.confidence,
                    elapsed_candles=elapsed_candles,
                    timeframe=timeframe,
                ),
            )
            for position, setup in enumerate(ordered, start=1)
        )

        return RankingSnapshot(
            timeframe=timeframe,
            at=at,
            rows=board,
            gate_passers=len(rows),
            below_floor=len(rows) - len(published),
        )

    async def _tier(self, symbol: str) -> UniverseTier:
        """The symbol's §1.4 tier, or INELIGIBLE when the registry has none.

        Not T3. An unknown tier is not a weak one, and §9.2's fourth key would
        otherwise rank an unregistered symbol above a genuinely tier-3 one.
        G1 should have refused it long before here; ordering it last is what
        keeps that true if the gate is ever relaxed.
        """
        state = await self._symbols.get_universe_state(symbol)

        return state.tier if state is not None else UniverseTier.INELIGIBLE


def _archetype_of(row: SetupRecord) -> Archetype:
    """The archetype a published setup must carry.

    T16's own check constraint refuses `floor_passed` without one, so this
    cannot fire against the table. It can fire against a fake or a future
    reader of some other source, and a board should say a record is
    contradictory rather than invent a rank for it.
    """
    if row.archetype is None:
        raise ValueError(f"{row.symbol}: published setup carries no archetype")

    return Archetype(row.archetype)
