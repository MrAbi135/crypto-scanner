"""§2.12's readiness: coverage and freshness are two questions.

The probe used to ask one. A feed this process had not yet seen a close on
reported `NO_DATA` and failed readiness — so after any restart every slow
timeframe was "not ready" until it next closed, with perfect data on disk the
whole time. On H4 that is up to four hours; on a daily series, a day.

Under Docker that is a cosmetic "unhealthy". Under the orchestrator TAD §22
targets it is a pod that never enters service — and on a liveness probe, a
restart loop that can never end, because the thing it waits for needs the
process to stay up.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scanner.application.marketdata.freshness import FreshnessState
from scanner.runtime.ingest import build_readiness_probe
from scanner.shared import Timeframe

NOW = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)

FEEDS = [("BTCUSDT", Timeframe.M5), ("BTCUSDT", Timeframe.H4)]


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeLiveIngest:
    """Observations this process has made, and the lag it measured."""

    def __init__(self, observed: dict | None = None) -> None:
        self.observed = observed or {}

    def has_observation(self, symbol: str, timeframe: Timeframe) -> bool:
        return (symbol, timeframe) in self.observed

    def freshness(self, symbol: str, timeframe: Timeframe) -> FreshnessState:
        return self.observed[(symbol, timeframe)]

    def detection_allowed(self, symbol: str, timeframe: Timeframe) -> bool:
        return self.observed[(symbol, timeframe)] is FreshnessState.FRESH


class FakeCandles:
    """The stored latest open time per feed — the coverage question."""

    def __init__(self, latest: dict | None = None) -> None:
        self.latest = latest or {}

    async def latest_open_time(self, symbol: str, timeframe: Timeframe):
        return self.latest.get((symbol, timeframe))

    async def fetch_series(self, *a, **k):
        return []


def probe(*, observed=None, latest=None, feeds=FEEDS):
    return build_readiness_probe(
        feeds=feeds,
        live_ingest=FakeLiveIngest(observed),
        candles=FakeCandles(latest),
        clock=FakeClock(),
    )


@pytest.mark.asyncio
async def test_a_covered_feed_with_no_observation_yet_is_ready() -> None:
    """The failure this fixes.

    H4's last close was two hours ago and the next is two hours away — the
    normal state of a four-hour series for most of every four hours. Nothing
    is wrong, and the probe used to say otherwise for the whole window.
    """
    ready, details = await probe(
        observed={("BTCUSDT", Timeframe.M5): FreshnessState.FRESH},
        latest={
            # Open time 00:00 → closed 04:00, two hours ago.
            ("BTCUSDT", Timeframe.H4): NOW - timedelta(hours=6),
        },
    )()

    assert ready
    assert details["feed:BTCUSDT:H4"] == "AWAITING_CLOSE"
    assert details["feed:BTCUSDT:M5"] == "FRESH"


@pytest.mark.asyncio
async def test_a_feed_with_no_stored_candles_is_still_not_ready() -> None:
    """`NO_DATA` now means what it says.

    Widening the probe must not make it unable to report the thing it was
    written for.
    """
    ready, details = await probe(
        observed={("BTCUSDT", Timeframe.M5): FreshnessState.FRESH},
        latest={},
    )()

    assert not ready
    assert details["feed:BTCUSDT:H4"] == "NO_DATA"


@pytest.mark.asyncio
async def test_a_feed_whose_arrivals_stopped_is_not_ready_and_says_so() -> None:
    """Distinguished from `NO_DATA`.

    "Nothing has ever arrived" and "arrivals stopped" call for different
    investigations, and a probe that reported both the same way would send
    someone to check the wrong thing.
    """
    ready, details = await probe(
        observed={("BTCUSDT", Timeframe.M5): FreshnessState.FRESH},
        latest={("BTCUSDT", Timeframe.H4): NOW - timedelta(days=2)},
    )()

    assert not ready
    assert details["feed:BTCUSDT:H4"] == "BEHIND"


@pytest.mark.asyncio
async def test_the_boundary_is_one_interval_of_slack() -> None:
    """Exactly one missed close is tolerated; two is not.

    The candle named by `latest` closes one interval later, and one further
    interval is the window in which the next close simply has not happened
    yet. Beyond that, one has been missed.
    """
    inside = await probe(
        latest={("BTCUSDT", Timeframe.H4): NOW - timedelta(hours=8)},
        feeds=[("BTCUSDT", Timeframe.H4)],
    )()

    # open 22:00 → closed 02:00 → four hours ago, exactly one interval.
    assert inside[0]
    assert inside[1]["feed:BTCUSDT:H4"] == "AWAITING_CLOSE"

    outside = await probe(
        latest={("BTCUSDT", Timeframe.H4): NOW - timedelta(hours=8, minutes=1)},
        feeds=[("BTCUSDT", Timeframe.H4)],
    )()

    assert not outside[0]
    assert outside[1]["feed:BTCUSDT:H4"] == "BEHIND"


@pytest.mark.asyncio
async def test_a_measured_lag_still_governs_once_observations_exist() -> None:
    """Coverage does not override freshness.

    A feed this process *is* watching, and which is lagging, must fail — the
    stored candles being present says nothing about whether the pipe is
    keeping up, which is what §2.12 measures.
    """
    ready, details = await probe(
        observed={("BTCUSDT", Timeframe.M5): FreshnessState.DEGRADED},
        latest={
            ("BTCUSDT", Timeframe.M5): NOW - timedelta(minutes=5),
            ("BTCUSDT", Timeframe.H4): NOW - timedelta(hours=6),
        },
    )()

    assert not ready
    assert details["feed:BTCUSDT:M5"] == "DEGRADED"


@pytest.mark.asyncio
async def test_every_feed_is_reported_even_when_one_fails() -> None:
    """An operator reading this wants the whole picture, not the first fault."""

    _, details = await probe(latest={})()

    assert set(details) == {"feed:BTCUSDT:M5", "feed:BTCUSDT:H4"}
