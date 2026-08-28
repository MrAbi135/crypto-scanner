"""§18.3's status strip — PRD FC-1.2's data honesty surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.repositories import IncidentRecord
from scanner.interfaces.api.app import build_read_api
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
    EmptyFeed,
    EmptyIncidents,
    EmptyRankings,
    EmptySignals,
    EmptySymbols,
    FakeSessionStore,
    FakeTenants,
    FakeUsers,
    bearer,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
AUTH = bearer(now=NOW)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeCandles(EmptySignals):
    def __init__(self, series=()) -> None:
        self._series = tuple(series)

    async def newest_per_series(self):
        return self._series


class FakeIncidents(EmptyIncidents):
    def __init__(self, rows=()) -> None:
        self._rows = tuple(rows)

    async def list_open(self, symbol=None):
        return self._rows


def build(candles=None, incidents=None) -> TestClient:
    store = FakeSessionStore()

    return TestClient(
        build_read_api(
            candles=candles or FakeCandles(),
            evidence=EmptySignals(),
            zones=EmptySignals(),
            pools=EmptySignals(),
            clock=FakeClock(),
            accounts=AccountService(FakeUsers(), FakeTenants()),
            sessions=SessionService(store),
            session_repository=store,
            access_tokens=AccessTokens(TEST_SECRET),
            signals=EmptySignals(),
            signal_transitions=EmptySignals(),
            outcomes=EmptySignals(),
            track_record=EmptySignals(),
            track_statistics=EmptySignals(),
            rankings=EmptyRankings(),
            feed=EmptyFeed(),
            incidents=incidents or FakeIncidents(),
            symbols=EmptySymbols(),
        )
    )


def get(client: TestClient):
    return client.get("/api/v1/dashboard/status", headers=AUTH)


def test_a_series_between_closes_is_not_behind() -> None:
    """The state a healthy slow timeframe is in for most of its life.

    A bare "is it current" check reports every timeframe as broken between
    closes, which is why the probe has three states rather than two.
    """
    # H4 candle opened four hours ago, so it closed exactly now.
    candles = FakeCandles([("BTCUSDT", Timeframe.H4, NOW - timedelta(hours=4))])

    body = get(build(candles)).json()["data"]

    assert body["feeds"][0]["coverage"] == "AWAITING_CLOSE"
    assert body["feeds"][0]["candles_behind"] == 0
    assert body["behind_count"] == 0


def test_a_series_that_stopped_says_how_far() -> None:
    """BEHIND by three closes and BEHIND by three hundred are the same state
    and not the same problem, so the count travels with it."""

    candles = FakeCandles([("BTCUSDT", Timeframe.H1, NOW - timedelta(hours=6))])

    feed = get(build(candles)).json()["data"]["feeds"][0]

    assert feed["coverage"] == "BEHIND"
    assert feed["candles_behind"] == 5


def test_the_behind_count_is_the_reason_to_read_the_list() -> None:
    candles = FakeCandles(
        [
            ("BTCUSDT", Timeframe.H1, NOW - timedelta(hours=6)),
            ("ETHUSDT", Timeframe.H1, NOW - timedelta(hours=1)),
        ]
    )

    body = get(build(candles)).json()["data"]

    assert body["behind_count"] == 1
    assert len(body["feeds"]) == 2


def test_open_incidents_are_the_degraded_set() -> None:
    incidents = FakeIncidents(
        [
            IncidentRecord(
                id="i1",
                scope_type="SYMBOL_TF",
                incident_type="GAP",
                started_at=NOW - timedelta(hours=2),
                symbol="ETHUSDT",
                timeframe=Timeframe.H1,
                candle_span=3,
            )
        ]
    )

    body = get(build(incidents=incidents)).json()["data"]

    assert body["degraded_count"] == 1
    assert body["degraded"][0]["symbol"] == "ETHUSDT"


def test_what_it_cannot_measure_is_named_rather_than_omitted() -> None:
    """A strip that quietly leaves out two of the four things §18.3 lists reads
    as a strip that checked them and found nothing wrong.

    The scan duration and the storm flag live in the engine process, which the
    API shares neither memory nor a cache with.
    """
    body = get(build()).json()["data"]

    joined = " ".join(body["not_measured"])

    assert "last_scan_cycle_ms" in joined
    assert "storm_mode" in joined
    assert "§2.12" in joined


def test_an_empty_platform_reports_no_feeds_rather_than_failing() -> None:
    body = get(build()).json()["data"]

    assert body["feeds"] == []
    assert body["behind_count"] == 0


def test_it_needs_a_token() -> None:
    assert build().get("/api/v1/dashboard/status").status_code == 401
