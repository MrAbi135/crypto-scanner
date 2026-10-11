"""A pass reads its candle window once (pass_candles.py), and nothing else changes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tests.golden.harness import runner
from tests.golden.harness.canonical import canonical_bytes
from tests.golden.harness.dataset import discover_datasets
from tests.support.builders import make_candle

from scanner.application.detection.pass_candles import PassScopedCandles
from scanner.application.detection.pipeline import DetectionPipeline
from scanner.shared import Timeframe

T0 = datetime(2026, 10, 11, tzinfo=UTC)
HOUR = timedelta(hours=1)


class CountingCandles:
    def __init__(self) -> None:
        self.calls = 0
        self.candles = [make_candle(open_time=T0 + i * HOUR) for i in range(5)]

    async def fetch_series(self, symbol, timeframe, start, end):
        self.calls += 1
        await asyncio.sleep(0)
        return [c for c in self.candles if start <= c.open_time < end]


@pytest.mark.asyncio
async def test_outside_a_pass_every_read_goes_through() -> None:
    inner = CountingCandles()
    candles = PassScopedCandles(inner)  # type: ignore[arg-type]

    await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)
    await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)

    assert inner.calls == 2


@pytest.mark.asyncio
async def test_inside_a_pass_one_window_is_read_once() -> None:
    inner = CountingCandles()
    candles = PassScopedCandles(inner)  # type: ignore[arg-type]

    async with candles.scope():
        first = await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)
        second = await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)
        other = await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 2 * HOUR)

    assert inner.calls == 2, "the repeated window was read twice"
    assert first == second
    assert len(other) == 2
    assert isinstance(first, tuple), "a shared window must not be mutable by one service"


@pytest.mark.asyncio
async def test_the_cache_ends_with_the_pass() -> None:
    """The next close's pass must read the database, never last pass's copy."""
    inner = CountingCandles()
    candles = PassScopedCandles(inner)  # type: ignore[arg-type]

    async with candles.scope():
        await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)

    async with candles.scope():
        await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)

    await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)

    assert inner.calls == 3


@pytest.mark.asyncio
async def test_two_concurrent_passes_do_not_share_a_cache() -> None:
    inner = CountingCandles()
    candles = PassScopedCandles(inner)  # type: ignore[arg-type]

    async def one_pass() -> None:
        async with candles.scope():
            await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)
            await candles.fetch_series("BTCUSDT", Timeframe.H1, T0, T0 + 5 * HOUR)

    await asyncio.gather(one_pass(), one_pass())

    assert inner.calls == 2, "each pass reads once -- not once between them, not four times"


_CONFLUENCE = [d for d in discover_datasets() if d.engine == "confluence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", _CONFLUENCE, ids=lambda d: d.dataset_id)
async def test_a_golden_pass_inside_the_scope_is_byte_identical(dataset, monkeypatch) -> None:
    """The whole pipeline, run with the shared window, against its label.

    Asserted to have actually shared reads -- otherwise this would pass by
    never entering the scope.
    """
    real = runner.InMemoryCandleRepository
    made: list[PassScopedCandles] = []
    reads = {"inner": 0}

    class Counting(real):  # type: ignore[misc, valid-type]
        async def fetch_series(self, *args, **kwargs):
            reads["inner"] += 1
            return await super().fetch_series(*args, **kwargs)

    def build(candles):
        wrapped = PassScopedCandles(Counting(candles))
        made.append(wrapped)
        return wrapped

    original_run = DetectionPipeline.run

    async def scoped(self, *args, **kwargs):
        async with made[-1].scope():
            return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(runner, "InMemoryCandleRepository", build)
    monkeypatch.setattr(DetectionPipeline, "run", scoped)

    actual = await runner.run_dataset(dataset)

    assert canonical_bytes(actual) == canonical_bytes(dataset.expected)
    assert reads["inner"] >= 1
    assert reads["inner"] < 9, f"{reads['inner']} reads: the window was not shared"
