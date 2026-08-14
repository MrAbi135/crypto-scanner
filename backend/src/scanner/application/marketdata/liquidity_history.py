"""Seven-day liquidity history aggregation (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from decimal import Decimal
from statistics import median

from scanner.application.marketdata.universe import LiquiditySnapshot
from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
    LiquidityHistoryRepository,
)

_REQUIRED_DAYS = 7


class InsufficientLiquidityHistoryError(ValueError):
    """Raised when fewer than seven daily observations are available."""


def build_liquidity_snapshot(
    records: list[LiquidityHistoryRecord],
) -> LiquiditySnapshot:
    """Build seven-day median liquidity metrics from daily observations."""

    if len(records) < _REQUIRED_DAYS:
        raise InsufficientLiquidityHistoryError(
            "at least 7 daily liquidity observations are required"
        )

    recent = records[:_REQUIRED_DAYS]

    return LiquiditySnapshot(
        median_daily_quote_volume=_decimal_median([record.daily_quote_volume for record in recent]),
        median_spread_bps=_decimal_median([record.spread_bps for record in recent]),
        median_depth_2pct=_decimal_median([record.depth_2pct for record in recent]),
    )


class LiquiditySnapshotBuilder:
    """Load recent liquidity history and build the SLS seven-day medians."""

    def __init__(
        self,
        history: LiquidityHistoryRepository,
    ) -> None:
        self._history = history

    async def build(
        self,
        exchange_symbol: str,
    ) -> LiquiditySnapshot:
        """Return the seven-day median snapshot for one symbol."""

        records = await self._history.fetch_recent(
            exchange_symbol,
            limit=_REQUIRED_DAYS,
        )

        return build_liquidity_snapshot(list(records))


def _decimal_median(
    values: list[Decimal],
) -> Decimal:
    """Return exact Decimal median without float conversion."""

    if not values:
        raise ValueError("cannot calculate median of empty values")

    return median(values)
