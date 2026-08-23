"""Read the published setups at one close and order them (SLS §9.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports.detection import EngineEventRepository
from scanner.application.ports.repositories import SymbolRepository
from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence import Archetype
from scanner.domain.ranking import RankableSetup, display_rank, rank
from scanner.shared import Timeframe

_PREFIX = "SETUP_CANDIDATE_"


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

    # Candidates seen but not published. §8.6 records below-floor candidates
    # "for calibration", and a board that reported only its rows would make a
    # quiet market and a broken pipeline look identical.
    considered: int
    unpublished: int


class RankingSnapshotService:
    """§9.2 over the candidates recorded at a single close.

    Reads `SETUP_CANDIDATE_*` events rather than a setups table, because that
    is where the confluence engine writes them today; S8's T16 table is still
    outstanding and this moves to it when it lands, without the ordering
    changing.
    """

    def __init__(
        self,
        events: EngineEventRepository,
        symbols: SymbolRepository,
    ) -> None:
        self._events = events
        self._symbols = symbols

    async def snapshot(
        self,
        symbols: tuple[str, ...],
        timeframe: Timeframe,
        at: datetime,
        *,
        elapsed_candles: int = 0,
    ) -> RankingSnapshot:
        setups: list[RankableSetup] = []
        considered = 0

        for symbol in symbols:
            tier = await self._tier(symbol)

            # A half-open window of one candle: `list_events` takes a range,
            # and a snapshot is one close.
            records = await self._events.list_events(
                symbol,
                timeframe,
                at,
                at + timeframe.duration,
            )

            for record in records:
                if not record.event_type.startswith(_PREFIX):
                    continue

                considered += 1

                payload = json.loads(record.payload)

                if not payload.get("publishable"):
                    continue

                setups.append(
                    RankableSetup(
                        symbol=symbol,
                        timeframe=timeframe,
                        confidence=Decimal(str(payload["confidence"])),
                        archetype=_archetype_of(payload, symbol),
                        tier=tier,
                        direction=record.event_type.removeprefix(_PREFIX),
                    )
                )

        ordered = rank(setups)

        rows = tuple(
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
            rows=rows,
            considered=considered,
            unpublished=considered - len(setups),
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


def _archetype_of(payload: dict[str, object], symbol: str) -> Archetype:
    """The archetype a published candidate must carry.

    §8.6 gives every archetype a confidence floor and `meets_floor` cannot
    pass a candidate without one, so `publishable` and a missing archetype
    cannot both be true. If they are, the record is contradictory and the
    board should say so rather than invent a rank for it.
    """
    raw = payload.get("archetype")

    if raw is None:
        raise ValueError(f"{symbol}: publishable candidate carries no archetype")

    return Archetype(raw)
