"""§18.7's incident row — DDD T8's data-honesty ledger."""

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

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
AUTH = bearer(now=NOW)


class FakeClock:
    def now(self) -> datetime:
        return NOW


def incident(
    incident_id: str,
    *,
    symbol: str | None = "ETHUSDT",
    started_at: datetime | None = None,
    resolved: bool = False,
) -> IncidentRecord:
    return IncidentRecord(
        id=incident_id,
        scope_type="SYMBOL_TF",
        incident_type="GAP",
        started_at=started_at or NOW - timedelta(hours=2),
        symbol=symbol,
        timeframe=Timeframe.H1,
        candle_span=3,
        resolution="backfilled" if resolved else None,
        resolved_at=(NOW - timedelta(hours=1)) if resolved else None,
        notes="three closes missing",
    )


class FakeIncidents(EmptyIncidents):
    def __init__(self, *records: IncidentRecord) -> None:
        self._records = records
        self.seen: dict[str, object] = {}

    async def list_ledger(self, *, symbol=None, open_only=False, limit=100):
        self.seen = {"symbol": symbol, "open_only": open_only, "limit": limit}

        rows = [
            row
            for row in self._records
            if (symbol is None or row.symbol == symbol)
            and (not open_only or row.resolved_at is None)
        ]

        return sorted(rows, key=lambda row: (row.started_at, row.id), reverse=True)


def build(incidents: FakeIncidents | None = None) -> TestClient:
    store = FakeSessionStore()

    app = build_read_api(
        candles=EmptySignals(),
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

    return TestClient(app)


def get(client: TestClient, **params):
    return client.get("/api/v1/market/incidents", params=params, headers=AUTH)


def test_the_ledger_renders_an_incident() -> None:
    body = get(build(FakeIncidents(incident("i1")))).json()

    row = body["data"][0]

    assert row["id"] == "i1"
    assert row["type"] == "GAP"
    assert row["symbol"] == "ETHUSDT"
    assert row["timeframe"] == "H1"
    assert row["candle_span"] == 3
    assert row["open"] is True
    assert row["resolved_at"] is None


def test_resolved_incidents_are_included_by_default() -> None:
    """An incident that was found and fixed is the part of the ledger that
    shows the honesty working. Defaulting to open-only would make a well-run
    week look like an empty one."""

    ledger = FakeIncidents(incident("open"), incident("fixed", resolved=True))

    body = get(build(ledger)).json()

    assert {row["id"] for row in body["data"]} == {"open", "fixed"}
    assert ledger.seen["open_only"] is False


def test_a_resolved_row_carries_both_the_when_and_the_what() -> None:
    """`resolved_at` alone leaves a reader guessing what was done; `resolution`
    alone does not say when it stopped."""

    row = get(build(FakeIncidents(incident("i1", resolved=True)))).json()["data"][0]

    assert row["open"] is False
    assert row["resolution"] == "backfilled"
    assert row["resolved_at"] is not None


def test_open_only_narrows_it() -> None:
    ledger = FakeIncidents(incident("open"), incident("fixed", resolved=True))

    body = get(build(ledger), open_only="true").json()

    assert [row["id"] for row in body["data"]] == ["open"]
    assert ledger.seen["open_only"] is True


def test_the_symbol_filter_reaches_the_query_not_the_response() -> None:
    """Filtering in the repository rather than after it. A ledger that read
    every incident and dropped most of them would grow with the history rather
    than with the answer."""

    ledger = FakeIncidents(incident("eth"), incident("btc", symbol="BTCUSDT"))

    body = get(build(ledger), symbol_id="BTCUSDT").json()

    assert [row["id"] for row in body["data"]] == ["btc"]
    assert ledger.seen["symbol"] == "BTCUSDT"


def test_newest_first() -> None:
    """A ledger that reorders itself between reads is not a ledger."""

    ledger = FakeIncidents(
        incident("old", started_at=NOW - timedelta(days=2)),
        incident("new", started_at=NOW - timedelta(minutes=5)),
    )

    body = get(build(ledger)).json()

    assert [row["id"] for row in body["data"]] == ["new", "old"]


def test_an_undocumented_parameter_is_refused() -> None:
    """§18.7 documents `symbol_id` and `open_only`, and §9 refuses a filter the
    server did not apply. Accepting `severity` and ignoring it would return an
    unfiltered ledger that claims to be filtered."""

    response = get(build(), severity="HIGH")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "severity"


def test_the_ledger_needs_a_token_but_not_an_operator() -> None:
    """§18.7: "public honesty -- not admin-gated", permissions `user`.

    A reader who can see a signal can see what was wrong with the data
    underneath it. The assertion here is that an ordinary user token is enough
    -- hiding the ledger behind an operator role would leave the signal looking
    better than the data it was computed from.
    """
    client = build(FakeIncidents(incident("i1")))

    assert client.get("/api/v1/market/incidents").status_code == 401
    assert client.get("/api/v1/market/incidents", headers=AUTH).status_code == 200


def test_a_clean_ledger_is_an_empty_list_not_an_error() -> None:
    body = get(build()).json()

    assert body["data"] == []
    assert body["page"]["count"] == 0
