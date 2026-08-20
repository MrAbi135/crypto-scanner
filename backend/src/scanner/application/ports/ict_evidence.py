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
class ShiftEvidenceRecord:
    """A §3.6 CHoCH or MSS, with the span its payload records."""

    event_type: str
    direction: str
    choch_index: int
    followthrough_index: int
    event_at: datetime
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

    # §5.1's Inputs name "BOS/MSS events", and `list_structure` cannot supply
    # them: it filters to SWING_*/STRUCTURE_*, so the ICT engine has never been
    # able to see an MSS. That is why `mss_origin` was passed as a literal.
    async def list_shifts(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[ShiftEvidenceRecord, ...]: ...

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[LiquidityEvidenceRecord, ...]: ...
