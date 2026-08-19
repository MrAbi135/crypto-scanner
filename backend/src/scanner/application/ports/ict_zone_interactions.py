"""Application ports for uniform ICT zone interactions (SLS §5.9)."""

from __future__ import annotations

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
