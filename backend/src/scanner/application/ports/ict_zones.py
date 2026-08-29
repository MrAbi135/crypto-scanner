"""Application ports for Sprint S6 ICT-zone persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class IctZoneRecord:
    zone_id: str
    symbol: str
    timeframe: Timeframe
    zone_type: str
    polarity: str
    state: str
    grade: str
    band_low: Decimal
    band_high: Decimal
    refined_low: Decimal | None
    refined_high: Decimal | None
    created_index: int
    confirmed_index: int
    created_at: datetime
    updated_at: datetime
    parent_zone_id: str | None
    dealing_range_id: str | None
    stale_context: bool
    gap_adjacent: bool
    origin_swept: bool | None
    evidence: str


@dataclass(frozen=True, slots=True)
class IctZoneTransitionRecord:
    transition_id: str
    zone_id: str
    symbol: str
    timeframe: Timeframe
    zone_type: str
    from_state: str
    to_state: str
    reason: str
    transitioned_at: datetime
    candle_index: int
    evidence: str


class IctZoneRepository(Protocol):
    async def upsert(
        self,
        zone: IctZoneRecord,
    ) -> None: ...

    async def get(
        self,
        zone_id: str,
    ) -> IctZoneRecord | None: ...

    async def list_live(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        only_versions: Mapping[str, str] | None = None,
    ) -> tuple[IctZoneRecord, ...]: ...

    async def transition(
        self,
        zone_id: str,
        *,
        from_state: str,
        to_state: str,
        updated_at: datetime,
    ) -> bool: ...


class IctZoneTransitionRepository(Protocol):
    async def append(
        self,
        transition: IctZoneTransitionRecord,
    ) -> bool: ...


class IctZoneStateStore(Protocol):
    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        zones: tuple[IctZoneRecord, ...],
    ) -> None: ...

    async def delete(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> None: ...
