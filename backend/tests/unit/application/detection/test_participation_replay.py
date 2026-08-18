"""Participation replay: what §6 and §7 record, and what they deliberately do not."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.application.detection.participation_replay import ParticipationReplayService
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)
BIG_QUOTE = Decimal("1000000")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, tzinfo=UTC)


class FakeCandleRepository:
    def __init__(self, series) -> None:
        self.series = list(series)

    async def fetch_series(self, symbol, timeframe, start, end):
        return self.series


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: dict[str, object] = {}

    async def append(self, record) -> bool:
        if record.event_key in self.events:
            return False

        self.events[record.event_key] = record
        return True

    def types(self) -> list[str]:
        return [r.event_type for r in self.events.values()]  # type: ignore[attr-defined]


def candle(index: int, *, volume: str = "10", quote: Decimal = BIG_QUOTE, close: str = "101"):
    return make_candle(
        timeframe=Timeframe.H4,
        open_time=BASE + Timeframe.H4.duration * index,
        open_=Decimal(100),
        close=Decimal(close),
        volume=Decimal(volume),
        quote_volume=quote,
    )


def service(series):
    repo = FakeEventRepository()

    return (
        ParticipationReplayService(FakeCandleRepository(series), repo, FakeClock()),
        repo,
    )


async def run(svc):
    return await svc.run(
        "BTCUSDT",
        Timeframe.H4,
        BASE,
        BASE + Timeframe.H4.duration * 500,
    )


@pytest.mark.asyncio
async def test_an_empty_window_records_nothing() -> None:
    svc, repo = service([])

    report = await run(svc)

    assert report.candles == 0
    assert repo.events == {}


@pytest.mark.asyncio
async def test_a_volume_spike_is_recorded_with_its_evidence() -> None:
    series = [candle(i) for i in range(20)] + [candle(20, volume="30")]

    svc, repo = service(series)

    report = await run(svc)

    assert report.volume_spikes == 1

    spike = next(r for r in repo.events.values() if r.event_type == "VOLUME_SPIKE")  # type: ignore[attr-defined]

    payload = json.loads(spike.payload)  # type: ignore[attr-defined]

    assert payload["rvol"] == "3"
    assert payload["rvol_class"] == "SPIKE"
    assert payload["direction"] == "UP"


@pytest.mark.asyncio
async def test_the_continuous_rvol_series_is_not_written_per_candle() -> None:
    """A class on every bar is a series, not a fact.

    Writing 500 rows per replay would bury the detection log in readings nobody
    queries, and the series stays computable from the candles it came from.
    """
    series = [candle(i) for i in range(60)]

    svc, repo = service(series)

    report = await run(svc)

    assert report.candles == 60
    assert "RVOL" not in " ".join(repo.types())
    assert len(repo.events) < 60


@pytest.mark.asyncio
async def test_a_replay_is_idempotent() -> None:
    """Re-running a window must not duplicate its facts.

    The engine re-processes a trailing window on every close, so a service that
    inserted afresh each time would multiply every event by the window length.
    """
    series = [candle(i) for i in range(20)] + [candle(20, volume="30")]

    svc, repo = service(series)

    first = await run(svc)
    second = await run(svc)

    assert first.events_inserted > 0
    assert second.events_inserted == 0
    assert len(repo.events) == first.events_inserted


@pytest.mark.asyncio
async def test_an_inverted_window_is_refused() -> None:
    svc, _ = service([candle(0)])

    with pytest.raises(ValueError, match="end must be greater"):
        await svc.run("BTCUSDT", Timeframe.H4, BASE, BASE)
