"""§18.4's universe view."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.repositories import UniverseRow
from scanner.domain.common.universe import UniverseTier
from scanner.interfaces.api.app import build_read_api
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


def row(
    symbol: str,
    *,
    tier: UniverseTier = UniverseTier.INELIGIBLE,
    status: str = "QUARANTINE",
    passes: int = 0,
    failures: int = 0,
) -> UniverseRow:
    return UniverseRow(
        exchange_symbol=symbol,
        base_asset=symbol[:-4],
        quote_asset="USDT",
        status=status,
        tier=tier,
        candidate_tier=None,
        consecutive_passes=passes,
        consecutive_failures=failures,
        first_seen_at=NOW,
    )


class FakeSymbols(EmptySymbols):
    def __init__(self, rows=(), observations=None) -> None:
        self._rows = tuple(rows)
        self._observations = observations or {}
        self.seen: dict[str, object] = {}

    async def list_universe(self, *, status=None, tier=None, limit=200):
        self.seen = {"status": status, "tier": tier, "limit": limit}

        return [
            r
            for r in self._rows
            if (status is None or r.status == status) and (tier is None or r.tier.value == tier)
        ]

    async def count_observations(self):
        return self._observations


def build(symbols: FakeSymbols | None = None) -> TestClient:
    store = FakeSessionStore()

    return TestClient(
        build_read_api(
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
            incidents=EmptyIncidents(),
            symbols=symbols or FakeSymbols(),
        )
    )


def get(client: TestClient, **params):
    return client.get("/api/v1/scanner/universe", params=params, headers=AUTH)


def test_a_symbol_still_collecting_is_not_a_symbol_that_failed() -> None:
    """The distinction this row exists for.

    Every symbol on a young host reads INELIGIBLE/QUARANTINE with zero passes,
    which is indistinguishable from a universe layer that has stopped — and was
    misread as exactly that. Seven observations are needed before an evaluation
    runs at all, so a symbol at four has not failed anything: it has not been
    assessed.
    """
    symbols = FakeSymbols([row("BTCUSDT")], {"BTCUSDT": 4})

    body = get(build(symbols)).json()

    assert body["data"][0]["observation_days"] == 4
    assert body["data"][0]["assessment"] == "collecting"


def test_a_symbol_past_the_history_gate_is_being_evaluated() -> None:
    symbols = FakeSymbols([row("BTCUSDT", passes=2)], {"BTCUSDT": 9})

    assert get(build(symbols)).json()["data"][0]["assessment"] == "evaluating"


def test_a_symbol_that_is_failing_says_so() -> None:
    symbols = FakeSymbols([row("BTCUSDT", failures=2)], {"BTCUSDT": 9})

    assert get(build(symbols)).json()["data"][0]["assessment"] == "failing"


def test_a_symbol_with_no_observations_reads_zero_not_absent() -> None:
    """Absent from the count map means none collected, which is a real answer
    and a different one from "the count was not taken"."""

    symbols = FakeSymbols([row("NEWUSDT")], {})

    assert get(build(symbols)).json()["data"][0]["observation_days"] == 0


def test_the_thresholds_travel_with_the_page() -> None:
    """Two counters and no threshold is a number without a denominator. §1.4's
    sevens are published so a reader can see what the counting is towards."""

    page = get(build(FakeSymbols([row("BTCUSDT")], {"BTCUSDT": 4}))).json()["page"]

    assert page["required_observation_days"] == 7
    assert page["required_promotion_days"] == 7


def test_the_filter_reaches_the_query() -> None:
    """Filtered in the repository rather than after it: the registry holds
    seven hundred rows and a page that read them all would grow with the
    exchange's listings rather than with the answer."""

    symbols = FakeSymbols([row("BTCUSDT"), row("OLDUSDT", status="DELISTED")], {})

    body = get(build(symbols), **{"filter[status]": "DELISTED"}).json()

    assert [r["symbol"] for r in body["data"]] == ["OLDUSDT"]
    assert symbols.seen["status"] == "DELISTED"


def test_category_is_refused_rather_than_ignored() -> None:
    """§18.4 documents a `category` filter and the registry has no such column
    — §1.4 tiers by liquidity and nothing classifies a symbol by sector.
    Accepting it would be a filter the server cannot apply, which §9 calls "a
    lie the client believes"."""

    response = get(build(), **{"filter[category]": "defi"})

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "filter[category]"


def test_a_client_sort_is_refused() -> None:
    response = get(build(), sort="-tier")

    assert response.status_code == 422


def test_it_needs_a_token() -> None:
    assert build().get("/api/v1/scanner/universe").status_code == 401
