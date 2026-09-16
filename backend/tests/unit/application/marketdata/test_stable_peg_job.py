"""The nightly §1.6 classifier pass for one symbol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.application.marketdata.stable_peg_job import StablePegJob
from scanner.domain.common import StableFlag
from scanner.shared import Timeframe

NOW = datetime(2026, 9, 17, 0, 4, 30, tzinfo=UTC)
MIDNIGHT = datetime(2026, 9, 17, tzinfo=UTC)


class _Candle:
    def __init__(self, close: str) -> None:
        self.close = Decimal(close)


class _Market:
    def __init__(self, closes: list[str]) -> None:
        self._closes = closes
        self.asked: tuple[object, ...] | None = None

    async def fetch_candles(self, symbol, timeframe, start, end, *, limit):
        self.asked = (symbol, timeframe, start, end, limit)
        return [_Candle(close) for close in self._closes]


class _Symbols:
    def __init__(self, flag: StableFlag | None = None) -> None:
        self.flag = flag
        self.saved: dict[str, object] | None = None

    async def get_stable_flag(self, exchange_symbol: str) -> StableFlag | None:
        return self.flag

    async def save_stable_peg(self, exchange_symbol, *, flag, deviation, checked_at) -> None:
        self.saved = {"flag": flag, "deviation": deviation, "checked_at": checked_at}


class _Clock:
    def now(self) -> datetime:
        return NOW


def _job(market: _Market, symbols: _Symbols) -> StablePegJob:
    return StablePegJob(market, symbols, _Clock())  # type: ignore[arg-type]


async def test_it_reads_thirty_closed_days_ending_at_midnight() -> None:
    """Never today's open candle: it would be a price, not a close."""
    market = _Market(["1"] * 30)

    await _job(market, _Symbols()).run_symbol("UUSDT")

    assert market.asked == (
        "UUSDT",
        Timeframe.D1,
        MIDNIGHT - timedelta(days=30),
        MIDNIGHT,
        30,
    )


async def test_a_pegged_symbol_is_flagged_and_its_measurement_saved() -> None:
    # UUSDT on 2026-09-17: RMS 0.042% from a dollar, on no curated list.
    symbols = _Symbols()

    report = await _job(_Market(["1.0004", "0.9996"] * 15), symbols).run_symbol("UUSDT")

    assert report.flag is StableFlag.FLAGGED
    assert symbols.saved == {
        "flag": StableFlag.FLAGGED,
        "deviation": Decimal("0.0004"),
        "checked_at": NOW,
    }


async def test_a_short_history_is_recorded_as_unmeasured_and_keeps_its_flag() -> None:
    symbols = _Symbols(StableFlag.FLAGGED)

    report = await _job(_Market(["1"] * 12), symbols).run_symbol("NEWUSDT")

    assert (report.closes, report.deviation, report.flag) == (12, None, StableFlag.FLAGGED)
    assert symbols.saved == {"flag": StableFlag.FLAGGED, "deviation": None, "checked_at": NOW}


async def test_a_dismissed_symbol_stays_dismissed_on_a_perfect_peg() -> None:
    symbols = _Symbols(StableFlag.DISMISSED)

    report = await _job(_Market(["1"] * 30), symbols).run_symbol("USDXUSDT")

    assert report.flag is StableFlag.DISMISSED
    assert symbols.saved is not None and symbols.saved["flag"] is StableFlag.DISMISSED
