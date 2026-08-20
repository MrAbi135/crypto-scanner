"""Application ports for uniform ICT zone interactions (SLS §5.9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class IctZoneInteractionRecord:
    interaction_id: str
    zone_id: str
    symbol: str
    timeframe: Timeframe
    zone_type: str
    kind: str
    observed_at: datetime
    candle_index: int
    penetration_depth: Decimal
    close_price: Decimal
    rejection_wick: Decimal
    close_through: bool
    evidence: str


class IctZoneInteractionRepository(Protocol):
    async def append(
        self,
        interaction: IctZoneInteractionRecord,
    ) -> bool: ...

    async def append_many(
        self,
        interactions: Sequence[IctZoneInteractionRecord],
    ) -> frozenset[str]:
        """Insert a batch in one transaction; return the ids actually written.

        One `append` per interaction meant one transaction per interaction. A
        real BTCUSDT H1 pass produced 57,427 of them -- 630 zones times their
        candle spans -- and spent most of its time committing, which is most of
        why a detection pass took four minutes against a target of under two.

        The ids come back rather than a count because `on_conflict_do_nothing`
        makes a re-run insert nothing, and the report's per-kind tallies have to
        stay honest about that: a replay that inserted zero must say zero.
        """
        ...

    # §5.9's interactions were write-only: the engine recorded every TOUCH,
    # REJECTION, RESPECT and CONFIRMATION and nothing could read one back. That
    # is what kept §8.6's A2 ("first retest with Respect") and §8.3.1's
    # entry-confirmation term out of reach -- both are questions about a
    # specific zone's history, and history you cannot query is not history.
    async def list_for_zone(
        self,
        zone_id: str,
    ) -> tuple[IctZoneInteractionRecord, ...]: ...


class IctZoneInteractionContextRepository(Protocol):
    async def list_zones(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]: ...

    async def list_transitions(
        self,
        zone_id: str,
    ) -> tuple[IctZoneTransitionRecord, ...]: ...

    async def list_transitions_for(
        self,
        zone_ids: Sequence[str],
    ) -> dict[str, tuple[IctZoneTransitionRecord, ...]]:
        """Every listed zone's transitions, in one query.

        Asked one zone at a time, a pass over 630 live zones makes 630 round
        trips before it evaluates a single candle -- and after the ATR and
        batching work that was the largest remaining cost in a detection pass,
        larger than anything the engine actually computes.
        """
        ...
