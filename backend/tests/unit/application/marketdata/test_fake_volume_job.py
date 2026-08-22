"""§6.6's daily evaluation, per symbol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.marketdata.fake_volume_job import FakeVolumeJob
from scanner.domain.common import TradeAggregate
from scanner.domain.volume import WashRiskState

DAY = datetime(2026, 8, 22, tzinfo=UTC)


class FakeSymbols:
    def __init__(self, state: WashRiskState | None = None) -> None:
        self.state = state or WashRiskState()
        self.saved: list[WashRiskState] = []

    async def get_wash_risk(self, exchange_symbol: str) -> WashRiskState:
        return self.state

    async def save_wash_risk(self, exchange_symbol: str, state: WashRiskState) -> None:
        self.saved.append(state)


class FakeTrades:
    def __init__(self, items: list[TradeAggregate] | None = None) -> None:
        self.items = items or []

    async def append_many(self, aggregates) -> int:
        return 0

    async def list_between(self, symbol, start, end):
        return tuple(i for i in self.items if start <= i.minute < end)


class FakeCounts:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    async def count(self, symbol, start, end) -> int:
        return self._count


def _minute(offset: int, mean: str, stddev: str, count: int = 10) -> TradeAggregate:
    return TradeAggregate(
        symbol="BTCUSDT",
        minute=DAY + timedelta(minutes=offset),
        taker_buy_volume=Decimal(5),
        taker_sell_volume=Decimal(5),
        trade_count=count,
        mean_trade_size=Decimal(mean),
        stddev_trade_size=Decimal(stddev),
        p90_trade_size=Decimal(mean),
        max_trade_size=Decimal(mean),
    )


def _job(**kwargs):
    symbols = kwargs.pop("symbols", FakeSymbols())
    trades = kwargs.pop("trades", FakeTrades())
    counts = kwargs.pop("counts", FakeCounts())

    return FakeVolumeJob(symbols, trades, counts), symbols  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_symbol_with_no_evidence_is_not_tagged() -> None:
    """Only §6.6(4) can answer without T4 or depth, and one test cannot tag."""
    job, symbols = _job()

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.measured == 1
    assert not report.tagged_today
    assert symbols.saved == [WashRiskState(False, 0)]


@pytest.mark.asyncio
async def test_two_failing_tests_tag_the_symbol() -> None:
    job, symbols = _job(
        counts=FakeCounts(9),
        trades=FakeTrades([_minute(0, "10", "0")]),
    )

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.excess_suspect_candles is True
    assert report.tests.trade_size_uniformity is True
    assert report.score == Decimal(50)
    assert symbols.saved == [WashRiskState(True, 0)]


@pytest.mark.asyncio
async def test_the_round_trip_test_uses_the_day_the_caller_supplies() -> None:
    job, _ = _job(counts=FakeCounts(9))

    report = await job.run_symbol(
        "BTCUSDT",
        DAY,
        daily_delta=Decimal(1),
        daily_volume=Decimal(1000),
        rvol_elevated=True,
    )

    assert report.tests.round_trip_symmetry is True
    assert report.tagged_today


@pytest.mark.asyncio
async def test_the_day_s_dispersion_is_pooled_not_averaged() -> None:
    """Two minutes of identical prints at different sizes are not a uniform day.

    Averaging the per-minute stddevs would report zero dispersion and trip
    §6.6(3); pooling keeps the variance *between* the minutes.
    """
    job, _ = _job(trades=FakeTrades([_minute(0, "1", "0"), _minute(1, "100", "0")]))

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.trade_size_uniformity is False


@pytest.mark.asyncio
async def test_a_clean_day_walks_the_hysteresis_down() -> None:
    job, symbols = _job(symbols=FakeSymbols(WashRiskState(tagged=True, clean_days=2)))

    report = await job.run_symbol("BTCUSDT", DAY)

    assert not report.tagged_today
    assert symbols.saved == [WashRiskState(False, 0)]
