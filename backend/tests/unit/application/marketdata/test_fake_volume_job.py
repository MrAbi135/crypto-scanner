"""§6.6's daily evaluation, per symbol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.marketdata.fake_volume_job import FakeVolumeJob
from scanner.domain.common import TradeAggregate
from scanner.domain.volume import WashRiskState
from scanner.shared import Timeframe
from tests.support.builders import make_candle

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


class FakeCandles:
    def __init__(self, series: list | None = None) -> None:
        self.series = series or []

    async def fetch_series(self, symbol, timeframe, start, end):
        return tuple(c for c in self.series if start <= c.open_time < end)


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
    candles = kwargs.pop("candles", FakeCandles())

    return FakeVolumeJob(symbols, trades, candles, counts), symbols  # type: ignore[arg-type]


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
async def test_a_symmetric_busy_day_is_read_from_stored_candles() -> None:
    """§6.6(2) is summed rather than fetched as a D1 bar: volume and taker-buy
    volume are additive, and the ingest does not subscribe to D1."""
    job, _ = _job(
        counts=FakeCounts(9),
        candles=FakeCandles(_quiet_days() + _balanced_day(volume="500")),
    )

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.round_trip_symmetry is True
    assert report.tagged_today


@pytest.mark.asyncio
async def test_the_same_symmetry_on_an_ordinary_day_does_not_trip() -> None:
    """Without elevated volume it is a quiet day, not a wash loop."""
    job, _ = _job(
        counts=FakeCounts(9),
        candles=FakeCandles(_quiet_days() + _balanced_day(volume="100")),
    )

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.round_trip_symmetry is False
    assert not report.tagged_today


@pytest.mark.asyncio
async def test_a_day_with_no_stored_candles_has_no_reading() -> None:
    """Not a symmetric tape -- no tape."""
    job, _ = _job(candles=FakeCandles(_quiet_days()))

    report = await job.run_symbol("BTCUSDT", DAY)

    assert report.tests.round_trip_symmetry is None


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


def _candle(open_time, volume: str, taker_buy: str):
    return make_candle(
        timeframe=Timeframe.M5,
        open_time=open_time,
        open_=Decimal(100),
        close=Decimal(100),
        volume=Decimal(volume),
        taker_buy_volume=Decimal(taker_buy),
    )


def _quiet_days() -> list:
    """Twenty prior days at 100 units each, so the baseline median is 100."""
    return [_candle(DAY - timedelta(days=back), "100", "50") for back in range(1, 21)]


def _balanced_day(*, volume: str) -> list:
    """One candle on the scored day, taker-balanced so |delta| is zero."""
    half = Decimal(volume) / 2

    return [_candle(DAY, volume, str(half))]
