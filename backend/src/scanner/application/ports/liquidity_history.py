"""Liquidity history persistence port (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LiquidityHistoryRecord:
    """One persisted daily liquidity observation."""

    exchange_symbol: str
    observed_at: datetime
    daily_quote_volume: Decimal
    spread_bps: Decimal
    depth_2pct: Decimal


class LiquidityHistoryRepository(Protocol):
    """Persistence contract for daily liquidity observations."""

    async def append(
        self,
        record: LiquidityHistoryRecord,
    ) -> None:
        """Persist one daily observation idempotently."""
        ...

    async def fetch_recent(
        self,
        exchange_symbol: str,
        *,
        limit: int = 7,
    ) -> Sequence[LiquidityHistoryRecord]:
        """Return most recent observations, newest first."""
        ...
