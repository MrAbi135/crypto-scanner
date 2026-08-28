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


@dataclass(frozen=True, slots=True)
class RecentSweepRecord:
    """One platform-wide sweep row for §18.3's dashboard.

    Carries its own symbol and timeframe because, unlike `list_liquidity`,
    nothing upstream has already fixed the context -- and the side comes from
    the pool the transition consumed, because "BSL swept" and "SSL swept" are
    opposite market statements and the transition row alone cannot tell them
    apart.
    """

    symbol: str
    timeframe: Timeframe
    pool_id: str
    side: str | None
    to_state: str
    reason: str
    transitioned_at: datetime
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

    async def list_recent_sweeps(
        self,
        *,
        limit: int,
    ) -> tuple[RecentSweepRecord, ...]: ...
