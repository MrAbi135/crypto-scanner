"""Folding a live aggTrade stream into T4 minute buckets (SLS §2.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.marketdata.trade_aggregator import TradeAggregator
from scanner.domain.common import TradeAggregate, TradePrint
from tests.support.clock import FakeClock

BASE = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)


class FakeAggregates:
    def __init__(self) -> None:
        self.rows: list[TradeAggregate] = []

    async def append_many(self, aggregates) -> int:
        self.rows.extend(aggregates)

        return len(aggregates)

    async def list_between(self, symbol, start, end):
        return tuple(r for r in self.rows if start <= r.minute < end)


def _print(second: int, size: str = "1") -> TradePrint:
    return TradePrint(
        at=BASE + timedelta(seconds=second),
        size=Decimal(size),
        taker_is_buyer=True,
    )


def _aggregator(now: datetime = BASE) -> tuple[TradeAggregator, FakeAggregates]:
    repo = FakeAggregates()

    return TradeAggregator(repo, FakeClock(now)), repo  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_minute_in_progress_is_never_written() -> None:
    """SLS §2 keeps no prints, so a bucket written early cannot be corrected."""
    aggregator, repo = _aggregator()

    for second in range(0, 50, 10):
        await aggregator.observe("BTCUSDT", _print(second))

    assert repo.rows == []


@pytest.mark.asyncio
async def test_a_print_in_the_next_minute_closes_the_previous_one() -> None:
    """The only end-of-minute signal the stream gives is the next minute."""
    aggregator, repo = _aggregator()

    await aggregator.observe("BTCUSDT", _print(10, "2"))
    await aggregator.observe("BTCUSDT", _print(20, "4"))

    assert repo.rows == []

    await aggregator.observe("BTCUSDT", _print(70))

    assert [row.minute for row in repo.rows] == [BASE]
    assert repo.rows[0].trade_count == 2
    assert repo.rows[0].taker_buy_volume == Decimal(6)


@pytest.mark.asyncio
async def test_a_quiet_symbol_still_gives_up_its_last_minute() -> None:
    """`observe` learns a minute ended only from the next print, and a market
    that stops printing sends none."""
    # The clock is already past the print's minute; `observe` still holds it,
    # because a print only ever closes minutes earlier than its own.
    aggregator, repo = _aggregator(BASE + timedelta(minutes=3))

    await aggregator.observe("BTCUSDT", _print(10))

    assert repo.rows == []

    assert await aggregator.flush_completed("BTCUSDT") == 1
    assert [row.minute for row in repo.rows] == [BASE]


@pytest.mark.asyncio
async def test_a_replayed_print_is_dropped_and_counted() -> None:
    """Reconnects replay. A print for a written minute cannot change it, and a
    silent drop is indistinguishable from a stream that never reconnected."""
    aggregator, repo = _aggregator()

    await aggregator.observe("BTCUSDT", _print(10))
    await aggregator.observe("BTCUSDT", _print(70))

    assert len(repo.rows) == 1

    assert await aggregator.observe("BTCUSDT", _print(20)) == 0

    assert len(repo.rows) == 1
    assert aggregator.dropped("BTCUSDT") == 1


@pytest.mark.asyncio
async def test_symbols_do_not_close_each_other_s_minutes() -> None:
    """One busy symbol must not seal a quiet one's minute on its behalf."""
    aggregator, repo = _aggregator()

    await aggregator.observe("BTCUSDT", _print(10))
    await aggregator.observe("ETHUSDT", _print(10))

    await aggregator.observe("BTCUSDT", _print(70))

    assert [row.symbol for row in repo.rows] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_a_gap_in_the_stream_closes_every_minute_it_skipped() -> None:
    aggregator, repo = _aggregator()

    await aggregator.observe("BTCUSDT", _print(10))
    await aggregator.observe("BTCUSDT", _print(70))
    await aggregator.observe("BTCUSDT", _print(600))

    assert [row.minute for row in repo.rows] == [BASE, BASE + timedelta(minutes=1)]
