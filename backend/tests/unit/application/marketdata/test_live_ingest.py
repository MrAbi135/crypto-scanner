"""Unit tests for Sprint S2 live candle ingest orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.marketdata.freshness import FreshnessState
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


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


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
class FakeBackfillReport:
    gaps_recorded: int = 0
    quarantined_batches: int = 0


@dataclass
class FakeBackfillService:
    candles: FakeCandleRepository
    calls: list[tuple[str, Timeframe, datetime, datetime]]
    report: FakeBackfillReport = field(default_factory=FakeBackfillReport)

    async def backfill(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FakeBackfillReport:
        self.calls.append((symbol, timeframe, start, end))
        self.candles.tail = end - timeframe.duration
        return self.report


def build_service(
    *,
    tail: datetime | None = None,
    now: datetime,
    report: FakeBackfillReport | None = None,
) -> tuple[
    LiveIngestService,
    FakeCandleRepository,
    FakeBackfillService,
]:
    repo = FakeCandleRepository(tail)
    backfill = FakeBackfillService(
        repo,
        [],
        report or FakeBackfillReport(),
    )
    service = LiveIngestService(
        repo,
        backfill,
        FakeClock(now),
    )
    return service, repo, backfill


@pytest.mark.asyncio
async def test_first_live_candle_is_inserted_and_fresh() -> None:
    now = datetime(2026, 8, 9, 0, 5, tzinfo=UTC)
    service, repo, backfill = build_service(now=now)

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 0, tzinfo=UTC),
    )

    inserted = await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

    assert inserted == 1
    assert repo.inserted == [candle]
    assert backfill.calls == []
    assert service.freshness("BTCUSDT", Timeframe.M5) is FreshnessState.FRESH
    assert service.detection_allowed("BTCUSDT", Timeframe.M5) is True


@pytest.mark.asyncio
async def test_duplicate_live_candle_is_ignored() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 9, 0, 5, tzinfo=UTC)

    service, repo, backfill = build_service(
        tail=tail,
        now=now,
    )

    candle = make_candle(open_time=tail)

    inserted = await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

    assert inserted == 0
    assert repo.inserted == []
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_contiguous_live_candle_is_inserted_without_backfill() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 9, 0, 10, tzinfo=UTC)

    service, repo, backfill = build_service(
        tail=tail,
        now=now,
    )

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 5, tzinfo=UTC),
    )

    inserted = await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

    assert inserted == 1
    assert repo.inserted == [candle]
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_gap_triggers_backfill_before_live_insert() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 9, 0, 20, tzinfo=UTC)

    service, repo, backfill = build_service(
        tail=tail,
        now=now,
    )

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 15, tzinfo=UTC),
    )

    inserted = await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

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


@pytest.mark.asyncio
async def test_unfillable_gap_marks_series_degraded() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 9, 0, 20, tzinfo=UTC)

    service, _, _ = build_service(
        tail=tail,
        now=now,
        report=FakeBackfillReport(
            gaps_recorded=1,
        ),
    )

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 15, tzinfo=UTC),
    )

    await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

    assert (
        service.freshness(
            "BTCUSDT",
            Timeframe.M5,
        )
        is FreshnessState.DEGRADED
    )
    assert (
        service.detection_allowed(
            "BTCUSDT",
            Timeframe.M5,
        )
        is False
    )


@pytest.mark.asyncio
async def test_quarantined_backfill_marks_series_suspect() -> None:
    tail = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 9, 0, 20, tzinfo=UTC)

    service, _, _ = build_service(
        tail=tail,
        now=now,
        report=FakeBackfillReport(
            quarantined_batches=1,
        ),
    )

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 15, tzinfo=UTC),
    )

    await service.ingest(
        candle,
        now - timedelta(seconds=1),
    )

    assert (
        service.freshness(
            "BTCUSDT",
            Timeframe.M5,
        )
        is FreshnessState.SUSPECT
    )
    assert (
        service.detection_allowed(
            "BTCUSDT",
            Timeframe.M5,
        )
        is False
    )


@pytest.mark.asyncio
async def test_stale_event_marks_series_stale() -> None:
    now = datetime(2026, 8, 9, 0, 5, tzinfo=UTC)
    service, _, _ = build_service(now=now)

    candle = make_candle(
        open_time=datetime(2026, 8, 9, 0, 0, tzinfo=UTC),
    )

    await service.ingest(
        candle,
        now - timedelta(seconds=6),
    )

    assert (
        service.freshness(
            "BTCUSDT",
            Timeframe.M5,
        )
        is FreshnessState.STALE
    )
    assert (
        service.detection_allowed(
            "BTCUSDT",
            Timeframe.M5,
        )
        is False
    )
