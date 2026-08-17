"""Boot-time warm-up backfill (Sprint S3b)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scanner.application.marketdata.warmup_backfill import WarmupBackfillService
from scanner.shared import Timeframe

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeCandleRepository:
    def __init__(self, counts: dict[tuple[str, Timeframe], int]) -> None:
        self.counts = counts

    async def count_series(self, symbol, timeframe, start, end) -> int:
        return self.counts.get((symbol, timeframe), 0)


class FakeBackfill:
    def __init__(self, repo: FakeCandleRepository, *, fills_to: int = 600) -> None:
        self.repo = repo
        self.fills_to = fills_to
        self.calls: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def backfill(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        self.repo.counts[(symbol, timeframe)] = self.fills_to
        return None


def build(counts, *, target: int = 600):
    repo = FakeCandleRepository(dict(counts))
    backfill = FakeBackfill(repo)

    service = WarmupBackfillService(repo, backfill, FakeClock(), target_candles=target)

    return service, repo, backfill


@pytest.mark.asyncio
async def test_an_empty_context_is_filled_to_the_target() -> None:
    service, _, backfill = build({})

    result = await service.warm("BTCUSDT", Timeframe.H1)

    assert result.candles_before == 0
    assert result.candles_after == 600
    assert result.skipped is False

    _, _, start, end = backfill.calls[0]

    assert end == NOW
    assert start == NOW - Timeframe.H1.duration * 600


@pytest.mark.asyncio
async def test_an_already_deep_context_is_left_alone() -> None:
    """Restarts are frequent; re-fetching 600 candles on each is waste."""
    service, _, backfill = build({("BTCUSDT", Timeframe.H1): 900})

    result = await service.warm("BTCUSDT", Timeframe.H1)

    assert result.skipped is True
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_a_partial_context_refetches_the_whole_window_not_the_shortfall() -> None:
    """The rows we hold may be an old island with a hole in front of them.

    Asking only for the difference would fill the near side and leave the hole
    permanently. ON CONFLICT DO NOTHING makes re-fetching known rows cheap.
    """
    service, _, backfill = build({("BTCUSDT", Timeframe.H1): 200})

    await service.warm("BTCUSDT", Timeframe.H1)

    _, _, start, _ = backfill.calls[0]

    assert start == NOW - Timeframe.H1.duration * 600


@pytest.mark.asyncio
async def test_contexts_are_warmed_lowest_timeframe_first() -> None:
    """Bottom-up, because a higher timeframe reads the one below it.

    Warming H1 before M15 leaves a window where H1 runs against an empty LTF
    and confirms nothing -- the exact failure that cost a debugging session on
    real BTC data.
    """
    service, _, backfill = build({})

    await service.warm_all(
        ("BTCUSDT", "ETHUSDT"),
        (Timeframe.M15, Timeframe.H1),
    )

    assert [timeframe for _, timeframe, _, _ in backfill.calls] == [
        Timeframe.M15,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H1,
    ]


@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_leave_the_universe_cold() -> None:
    service, repo, backfill = build({})

    original = backfill.backfill

    async def sometimes_fail(symbol, timeframe, start, end):
        if symbol == "BADUSDT":
            raise RuntimeError("delisted")

        return await original(symbol, timeframe, start, end)

    backfill.backfill = sometimes_fail  # type: ignore[method-assign]

    results = await service.warm_all(
        ("BADUSDT", "BTCUSDT"),
        (Timeframe.H1,),
    )

    assert [result.symbol for result in results] == ["BTCUSDT"]
    assert repo.counts[("BTCUSDT", Timeframe.H1)] == 600
