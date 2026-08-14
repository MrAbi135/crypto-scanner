"""Daily liquidity observation collection (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scanner.application.marketdata.liquidity import (
    calculate_liquidity_metrics,
)
from scanner.application.ports import Clock, MarketDataProvider
from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
    LiquidityHistoryRepository,
)
from scanner.application.ports.liquidity_provider import (
    LiquidityDataProvider,
)
from scanner.shared import Timeframe


class DailyLiquidityCollector:
    """Collect one UTC-day liquidity observation for one symbol."""

    def __init__(
        self,
        market_data: MarketDataProvider,
        liquidity_data: LiquidityDataProvider,
        history: LiquidityHistoryRepository,
        clock: Clock,
    ) -> None:
        self._market_data = market_data
        self._liquidity_data = liquidity_data
        self._history = history
        self._clock = clock

    async def collect(
        self,
        exchange_symbol: str,
    ) -> LiquidityHistoryRecord:
        """Collect and persist the previous closed UTC day's metrics."""

        observed_at = _utc_day_start(self._clock.now())

        day_start = observed_at - timedelta(days=1)
        day_end = observed_at

        candles = await self._market_data.fetch_candles(
            exchange_symbol,
            Timeframe.D1,
            day_start,
            day_end,
            limit=1,
        )

        if len(candles) != 1:
            raise ValueError(f"expected exactly one closed D1 candle for {exchange_symbol}")

        candle = candles[0]

        top = await self._liquidity_data.fetch_top_of_book(exchange_symbol)

        book = await self._liquidity_data.fetch_order_book(
            exchange_symbol,
            limit=1000,
        )

        metrics = calculate_liquidity_metrics(
            top,
            book,
        )

        record = LiquidityHistoryRecord(
            exchange_symbol=exchange_symbol,
            observed_at=observed_at,
            daily_quote_volume=candle.quote_volume,
            spread_bps=metrics.spread_bps,
            depth_2pct=metrics.depth_2pct,
        )

        await self._history.append(record)

        return record


def _utc_day_start(
    value: datetime,
) -> datetime:
    """Return the UTC midnight containing value."""

    if value.tzinfo is None:
        raise ValueError("clock must return timezone-aware datetime")

    utc_value = value.astimezone(UTC)

    return utc_value.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
