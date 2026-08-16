"""Read-only evidence ports consumed by the Sprint S6 ICT engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class StructureEvidenceRecord:
    event_type: str
    event_at: datetime
    algo_version: str
    payload: str


@dataclass(frozen=True, slots=True)
class LiquidityEvidenceRecord:
    pool_id: str
    from_state: str
    to_state: str
    reason: str
    transitioned_at: datetime
    candle_index: int
    evidence: str


class IctEvidenceRepository(Protocol):
    async def list_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[StructureEvidenceRecord, ...]: ...

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[LiquidityEvidenceRecord, ...]: ...
