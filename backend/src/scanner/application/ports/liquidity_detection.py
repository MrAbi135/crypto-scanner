"""Ports owned by the Sprint S5 liquidity-detection engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class LiquidityPoolRecord:
    pool_id: str
    symbol: str
    timeframe: Timeframe
    side: str
    liquidity_class: str
    source: str
    price: Decimal
    band_low: Decimal
    band_high: Decimal
    strength: Decimal
    state: str
    member_count: int
    created_index: int
    created_at: datetime
    updated_at: datetime
    evidence: str


@dataclass(frozen=True, slots=True)
class LiquidityTransitionRecord:
    transition_id: str
    pool_id: str
    symbol: str
    timeframe: Timeframe
    from_state: str
    to_state: str
    reason: str
    transitioned_at: datetime
    candle_index: int
    evidence: str


class LiquidityPoolRepository(Protocol):
    async def upsert(
        self,
        pool: LiquidityPoolRecord,
    ) -> None: ...

    async def upsert_many(
        self,
        pools: Sequence[LiquidityPoolRecord],
    ) -> None:
        """`upsert` for each pool, as one write.

        The same rule per row -- the mutable subset changes only while the
        pool is ACTIVE -- and an id given twice ends as its last occurrence,
        as two sequential upserts would leave it.
        """
        ...

    async def get(
        self,
        pool_id: str,
    ) -> LiquidityPoolRecord | None: ...

    async def get_many(
        self,
        pool_ids: Sequence[str],
    ) -> dict[str, LiquidityPoolRecord]:
        """`get` for each id, as one read; an id with no row is absent."""
        ...

    async def list_active(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        only_version: str | None = None,
    ) -> tuple[LiquidityPoolRecord, ...]: ...

    async def transition(
        self,
        pool_id: str,
        *,
        to_state: str,
        updated_at: datetime,
    ) -> bool: ...


class LiquidityTransitionRepository(Protocol):
    async def append(
        self,
        transition: LiquidityTransitionRecord,
    ) -> bool: ...


class LiquidityStateStore(Protocol):
    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        pools: tuple[LiquidityPoolRecord, ...],
    ) -> None: ...

    async def delete(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> None: ...
