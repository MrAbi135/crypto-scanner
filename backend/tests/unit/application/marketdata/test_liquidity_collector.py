"""Unit tests for daily liquidity observation collection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.marketdata.liquidity_collector import (
    DailyLiquidityCollector,
)
from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
)
from scanner.application.ports.liquidity_provider import (
    OrderBookLevel,
    OrderBookSnapshot,
    TopOfBook,
)
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe


class FakeClock:
    def __init__(
        self,
        now: datetime,
    ) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeMarketDataProvider:
    def __init__(
        self,
        candles: list[Candle],
    ) -> None:
        self.candles = candles
        self.calls: list[
            tuple[
                str,
                Timeframe,
                datetime,
                datetime,
                int,
            ]
        ] = []

    async def fetch_candles(
        self,
        exchange_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        limit: int,
    ) -> list[Candle]:
        self.calls.append(
            (
                exchange_symbol,
                timeframe,
                start,
                end,
                limit,
            )
        )

        return self.candles


class FakeLiquidityDataProvider:
    def __init__(self) -> None:
        self.top_calls: list[str] = []
        self.book_calls: list[tuple[str, int]] = []

    async def fetch_top_of_book(
        self,
        exchange_symbol: str,
    ) -> TopOfBook:
        self.top_calls.append(exchange_symbol)

        return TopOfBook(
            exchange_symbol=exchange_symbol,
            bid_price=Decimal("99"),
            bid_quantity=Decimal("1"),
            ask_price=Decimal("101"),
            ask_quantity=Decimal("1"),
        )

    async def fetch_order_book(
        self,
        exchange_symbol: str,
        *,
        limit: int = 1000,
    ) -> OrderBookSnapshot:
        self.book_calls.append(
            (
                exchange_symbol,
                limit,
            )
        )

        return OrderBookSnapshot(
            exchange_symbol=exchange_symbol,
            bids=(
                OrderBookLevel(
                    price=Decimal("100"),
                    quantity=Decimal("2"),
                ),
                OrderBookLevel(
                    price=Decimal("99"),
                    quantity=Decimal("3"),
                ),
                OrderBookLevel(
                    price=Decimal("97"),
                    quantity=Decimal("10"),
                ),
            ),
            asks=(
                OrderBookLevel(
                    price=Decimal("100"),
                    quantity=Decimal("4"),
                ),
                OrderBookLevel(
                    price=Decimal("101"),
                    quantity=Decimal("5"),
                ),
                OrderBookLevel(
                    price=Decimal("103"),
                    quantity=Decimal("10"),
                ),
            ),
        )


class FakeLiquidityHistoryRepository:
    def __init__(self) -> None:
        self.saved: list[LiquidityHistoryRecord] = []

    async def append(
        self,
        record: LiquidityHistoryRecord,
    ) -> None:
        self.saved.append(record)


def daily_candle(
    *,
    symbol: str = "BTCUSDT",
    open_time: datetime = datetime(
        2026,
        8,
        8,
        tzinfo=UTC,
    ),
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.D1,
        open_time=open_time,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1000"),
        quote_volume=Decimal("125000000"),
        taker_buy_volume=Decimal("500"),
        trade_count=100,
        source=CandleSource.BACKFILL,
    )


@pytest.mark.asyncio
async def test_collect_persists_daily_liquidity_observation() -> None:
    market_data = FakeMarketDataProvider(
        [
            daily_candle(),
        ]
    )
    liquidity_data = FakeLiquidityDataProvider()
    history = FakeLiquidityHistoryRepository()

    collector = DailyLiquidityCollector(
        market_data,
        liquidity_data,
        history,
        FakeClock(
            datetime(
                2026,
                8,
                9,
                3,
                45,
                tzinfo=UTC,
            )
        ),
    )

    record = await collector.collect("BTCUSDT")

    assert market_data.calls == [
        (
            "BTCUSDT",
            Timeframe.D1,
            datetime(
                2026,
                8,
                8,
                tzinfo=UTC,
            ),
            datetime(
                2026,
                8,
                9,
                tzinfo=UTC,
            ),
            1,
        )
    ]

    assert liquidity_data.top_calls == ["BTCUSDT"]
    assert liquidity_data.book_calls == [
        (
            "BTCUSDT",
            1000,
        )
    ]

    assert record == LiquidityHistoryRecord(
        exchange_symbol="BTCUSDT",
        observed_at=datetime(
            2026,
            8,
            9,
            tzinfo=UTC,
        ),
        daily_quote_volume=Decimal("125000000"),
        spread_bps=Decimal("200"),
        depth_2pct=Decimal("1402"),
    )

    assert history.saved == [record]


@pytest.mark.asyncio
async def test_collect_uses_previous_closed_utc_day() -> None:
    market_data = FakeMarketDataProvider(
        [
            daily_candle(
                open_time=datetime(
                    2026,
                    8,
                    8,
                    tzinfo=UTC,
                )
            ),
        ]
    )

    collector = DailyLiquidityCollector(
        market_data,
        FakeLiquidityDataProvider(),
        FakeLiquidityHistoryRepository(),
        FakeClock(
            datetime(
                2026,
                8,
                9,
                23,
                59,
                59,
                tzinfo=UTC,
            )
        ),
    )

    await collector.collect("BTCUSDT")

    call = market_data.calls[0]

    assert call[2] == datetime(
        2026,
        8,
        8,
        tzinfo=UTC,
    )
    assert call[3] == datetime(
        2026,
        8,
        9,
        tzinfo=UTC,
    )


@pytest.mark.asyncio
async def test_collect_converts_clock_time_to_utc_midnight() -> None:
    from datetime import timedelta, timezone

    pakistan_time = timezone(timedelta(hours=5))

    market_data = FakeMarketDataProvider(
        [
            daily_candle(),
        ]
    )

    history = FakeLiquidityHistoryRepository()

    collector = DailyLiquidityCollector(
        market_data,
        FakeLiquidityDataProvider(),
        history,
        FakeClock(
            datetime(
                2026,
                8,
                9,
                8,
                30,
                tzinfo=pakistan_time,
            )
        ),
    )

    record = await collector.collect("BTCUSDT")

    assert record.observed_at == datetime(
        2026,
        8,
        9,
        tzinfo=UTC,
    )


@pytest.mark.asyncio
async def test_collect_rejects_missing_daily_candle() -> None:
    collector = DailyLiquidityCollector(
        FakeMarketDataProvider([]),
        FakeLiquidityDataProvider(),
        FakeLiquidityHistoryRepository(),
        FakeClock(
            datetime(
                2026,
                8,
                9,
                tzinfo=UTC,
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="expected exactly one closed D1 candle for BTCUSDT",
    ):
        await collector.collect("BTCUSDT")


@pytest.mark.asyncio
async def test_collect_rejects_multiple_daily_candles() -> None:
    collector = DailyLiquidityCollector(
        FakeMarketDataProvider(
            [
                daily_candle(),
                daily_candle(),
            ]
        ),
        FakeLiquidityDataProvider(),
        FakeLiquidityHistoryRepository(),
        FakeClock(
            datetime(
                2026,
                8,
                9,
                tzinfo=UTC,
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="expected exactly one closed D1 candle for BTCUSDT",
    ):
        await collector.collect("BTCUSDT")


@pytest.mark.asyncio
async def test_collect_rejects_naive_clock_datetime() -> None:
    collector = DailyLiquidityCollector(
        FakeMarketDataProvider(
            [
                daily_candle(),
            ]
        ),
        FakeLiquidityDataProvider(),
        FakeLiquidityHistoryRepository(),
        FakeClock(
            datetime(
                2026,
                8,
                9,
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="clock must return timezone-aware datetime",
    ):
        await collector.collect("BTCUSDT")
