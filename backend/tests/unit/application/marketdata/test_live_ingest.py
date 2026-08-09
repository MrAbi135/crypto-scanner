"""Unit tests for Sprint S2 live candle ingest orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.marketdata.live_ingest import LiveIngestService
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe


def make_candle(
    *,
    symbol: str = "BTCUSDT",
    open_time: datetime,
    timeframe: Timeframe = Timeframe.M5,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        taker_buy_volume=Decimal("6"),
        trade_count=100,
        source=CandleSource.STREAM,
    )


class FakeCandleRepository:
    def __init__(self, tail: datetime | None = None) -> None:
        self.tail = tail
        self.inserted: list[Candle] = []

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None:
        return self.tail

    async def bulk_insert(self, candles: list[Candle]) -> int:
        self.inserted.extend(candles)

        if candles:
            self.tail = candles[-1].open_time

        return len(candles)


@dataclass
class FakeBackfillService:
    candles: FakeCandleRepository
    calls: list[tuple[str, Timeframe, datetime, datetime]]

    async def backfill(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> object:
        self.calls.append((symbol, timeframe, start, end))
        self.candles.tail = end - timeframe.duration
        return object()


@pytest.mark.asyncio
async def test_first_live_candle_is_inserted() -> None:
    repo = FakeCandleRepository()
    backfill = FakeBackfillService(repo, [])
    service = LiveIngestService(repo, backfill)

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 0, tzinfo=UTC),
    )

    inserted = await service.ingest(candle)

    assert inserted == 1
    assert repo.inserted == [candle]
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_duplicate_live_candle_is_ignored() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    repo = FakeCandleRepository(tail)
    backfill = FakeBackfillService(repo, [])
    service = LiveIngestService(repo, backfill)

    candle = make_candle(open_time=tail)

    inserted = await service.ingest(candle)

    assert inserted == 0
    assert repo.inserted == []
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_contiguous_live_candle_is_inserted_without_backfill() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    repo = FakeCandleRepository(tail)
    backfill = FakeBackfillService(repo, [])
    service = LiveIngestService(repo, backfill)

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 5, tzinfo=UTC),
    )

    inserted = await service.ingest(candle)

    assert inserted == 1
    assert repo.inserted == [candle]
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_gap_triggers_backfill_before_live_insert() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    repo = FakeCandleRepository(tail)
    backfill = FakeBackfillService(repo, [])
    service = LiveIngestService(repo, backfill)

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 15, tzinfo=UTC),
    )

    inserted = await service.ingest(candle)

    assert inserted == 1
    assert backfill.calls == [
        (
            "BTCUSDT",
            Timeframe.M5,
            datetime(2026, 8, 9, 0, 5, tzinfo=UTC),
            datetime(2026, 8, 9, 0, 15, tzinfo=UTC),
        )
    ]
    assert repo.inserted == [candle]
