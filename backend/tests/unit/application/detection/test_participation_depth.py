"""§6.4's depth input (SLS v1.0.20) in the engines that run the check.

Participation records VOLUME_SUSPECT; confluence scores the newest candle.
Both read the depth through `DepthInput`, so they must agree.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryLiquidityHistoryRepository
from tests.unit.application.detection.test_participation_replay import (
    BASE,
    FakeCandleRepository,
    FakeClock,
    FakeEventRepository,
    candle,
    run,
)

from scanner.application.detection.confluence_replay import _read_participation
from scanner.application.detection.depth_input import DepthInput
from scanner.application.detection.participation_replay import ParticipationReplayService
from scanner.application.ports.liquidity_history import LiquidityHistoryRecord
from scanner.domain.common.rvol import relative_volumes

DAY = timedelta(days=1)

# Candle 20 (H4) opens 2026-01-04 08:00 and closes 12:00, so the newest stamp
# it may read is 2026-01-03 (owner ruling M2 Q1).
ABNORMAL_CLOSE = BASE + 21 * timedelta(hours=4)
LAST_READABLE = datetime(2026, 1, 3, tzinfo=UTC)


def history(latest: str, *, others: str = "100", days: int = 7, symbol: str = "BTCUSDT"):
    stamps = [LAST_READABLE - i * DAY for i in range(days)][::-1]
    rows = [
        LiquidityHistoryRecord(symbol, s, Decimal(1), Decimal(1), Decimal(others)) for s in stamps
    ]
    rows[-1] = LiquidityHistoryRecord(symbol, stamps[-1], Decimal(1), Decimal(1), Decimal(latest))
    return rows


class CountingHistory(InMemoryLiquidityHistoryRepository):
    def __init__(self, records) -> None:
        super().__init__(records)
        self.calls = 0

    async def fetch_until(self, exchange_symbol, until, *, limit=7):
        self.calls += 1
        return await super().fetch_until(exchange_symbol, until, limit=limit)


def abnormal_series(trades: int):
    return [candle(i) for i in range(20)] + [candle(20, volume="60", trades=trades)]


async def suspects(series, records):
    repo = FakeEventRepository()
    svc = ParticipationReplayService(
        FakeCandleRepository(series),
        repo,
        FakeClock(),
        liquidity_history=InMemoryLiquidityHistoryRepository(records),
    )
    await run(svc)
    return [
        json.loads(r.payload)  # type: ignore[attr-defined]
        for r in repo.events.values()
        if r.event_type == "VOLUME_SUSPECT"  # type: ignore[attr-defined]
    ]


@pytest.mark.asyncio
async def test_thin_book_on_an_abnormal_candle_is_tagged_suspect() -> None:
    # Many participants, but the book is under half its 7-day median.
    tagged = await suspects(abnormal_series(trades=40), history(latest="49"))

    assert len(tagged) == 1
    assert tagged[0]["participants_ok"] is True
    assert tagged[0]["depth_ok"] is False
    assert tagged[0]["validated"] is True


@pytest.mark.asyncio
async def test_a_book_at_half_its_median_passes_and_the_check_completes() -> None:
    # The floor is inclusive: depth >= 50% of the 7-day median passes.
    assert await suspects(abnormal_series(trades=40), history(latest="50")) == []


@pytest.mark.asyncio
async def test_a_snapshot_stamped_on_the_closing_day_is_not_read() -> None:
    # Only a thin snapshot on the candle's own day: it is not readable, the six
    # before it are out of reach, so depth has no verdict and nothing is tagged.
    rows = [
        *history(latest="100", days=6),
        LiquidityHistoryRecord("BTCUSDT", LAST_READABLE + DAY, Decimal(1), Decimal(1), Decimal(1)),
    ]

    assert await suspects(abnormal_series(trades=40), rows) == []


@pytest.mark.asyncio
async def test_fewer_than_seven_snapshots_leaves_depth_without_a_verdict() -> None:
    tagged = await suspects(abnormal_series(trades=10), history(latest="1", days=6))

    # Tagged for participants only; the book half is unknown, not failed.
    assert len(tagged) == 1
    assert tagged[0]["participants_ok"] is False
    assert tagged[0]["depth_ok"] is None
    assert tagged[0]["validated"] is False


@pytest.mark.asyncio
async def test_only_an_abnormal_candle_reads_the_book() -> None:
    counting = CountingHistory(history(latest="100"))
    series = [candle(i) for i in range(20)] + [candle(20, volume="30")]
    svc = ParticipationReplayService(
        FakeCandleRepository(series), FakeEventRepository(), FakeClock(), liquidity_history=counting
    )

    await run(svc)

    assert counting.calls == 0


@pytest.mark.asyncio
async def test_depth_input_reads_once_per_symbol_per_day() -> None:
    counting = CountingHistory(history(latest="100"))
    depth = DepthInput(counting)

    first = await depth.at("BTCUSDT", ABNORMAL_CLOSE)
    again = await depth.at("BTCUSDT", ABNORMAL_CLOSE + timedelta(hours=4))
    next_day = await depth.at("BTCUSDT", ABNORMAL_CLOSE + DAY)

    assert first is not None and first == again
    assert next_day is not None
    assert counting.calls == 2


@pytest.mark.asyncio
async def test_depth_input_without_a_repository_reads_nothing() -> None:
    assert await DepthInput(None).at("BTCUSDT", ABNORMAL_CLOSE) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("latest", "trades"), [("49", 40), ("50", 40), ("49", 10), ("100", 10)])
async def test_confluence_reaches_the_verdict_participation_records(latest, trades) -> None:
    series = abnormal_series(trades=trades)
    rows = history(latest=latest)

    recorded = await suspects(series, rows)
    reading = _read_participation(
        series,
        (),
        None,
        relative_volumes(series, ()),
        await DepthInput(InMemoryLiquidityHistoryRepository(rows)).at(
            "BTCUSDT", series[-1].close_time
        ),
    )

    assert reading.suspect is bool(recorded)


@pytest.mark.asyncio
async def test_confluence_marks_an_abnormal_candle_without_depth_incomplete() -> None:
    # Participants pass, no depth history: validated is False, so confluence
    # must cap the volume factor, yet nothing is a suspect.
    series = abnormal_series(trades=40)

    reading = _read_participation(series, (), None, relative_volumes(series, ()), None)

    assert reading.incomplete is True
    assert reading.suspect is False


@pytest.mark.asyncio
async def test_confluence_marks_a_fully_checked_candle_complete() -> None:
    series = abnormal_series(trades=40)
    rows = history(latest="100")
    depth = await DepthInput(InMemoryLiquidityHistoryRepository(rows)).at(
        "BTCUSDT", series[-1].close_time
    )

    reading = _read_participation(series, (), None, relative_volumes(series, ()), depth)

    assert reading.incomplete is False
    assert reading.suspect is False


def test_a_quiet_candle_is_neither_incomplete_nor_suspect() -> None:
    series = [candle(i) for i in range(21)]

    reading = _read_participation(series, (), None, relative_volumes(series, ()), None)

    assert reading.incomplete is False
    assert reading.suspect is False
