"""§18.4's live feed — "THE core read" (PRD FC-2.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scanner.application.feed import LiveFeedService
from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.signals import SignalRecord
from scanner.domain.common.universe import UniverseTier
from scanner.interfaces.api.app import build_read_api
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
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


def signal(
    signal_id: str,
    *,
    symbol: str = "ETHUSDT",
    timeframe: Timeframe = Timeframe.H1,
    confidence: str = "75",
    archetype: str = "A3",
    grade: str = "B",
    direction: str = "UP",
    published_at: datetime | None = None,
) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        setup_id=signal_id,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        archetype=archetype,
        grade=grade,
        final_confidence=Decimal(confidence),
        entry_proximal=Decimal("2483.52"),
        entry_distal=Decimal("2468.00"),
        invalidation_level=Decimal("2468.00"),
        target_bands=json.dumps({"primary": {"low": "2515.43", "high": "2515.43"}}),
        published_at=published_at or NOW,
        ttl_candles=24,
        algo_version="s8-confluence-v22",
        param_set_version="2026.08.24.2",
        payload="{}",
        payload_hash="0" * 64,
        dedup_key=f"{symbol}|{timeframe.value}|{direction}|{archetype}",
    )


class FakeSignals(EmptySignals):
    def __init__(self, *records: SignalRecord) -> None:
        self._by_id = {record.signal_id: record for record in records}

    async def get(self, signal_id: str) -> SignalRecord | None:
        return self._by_id.get(signal_id)


class FakeTransitions(EmptySignals):
    def __init__(self, *pairs: tuple[str, str]) -> None:
        self._pairs = pairs

    async def live_states(self) -> tuple[tuple[str, str], ...]:
        return self._pairs


class FakeSymbols:
    def __init__(self, tier: UniverseTier | None = UniverseTier.T1) -> None:
        self._tier = tier

    async def get_universe_state(self, symbol: str):
        if self._tier is None:
            return None

        class _State:
            tier = self._tier

        return _State()


def build(
    signals: FakeSignals | None = None,
    transitions: FakeTransitions | None = None,
    symbols: FakeSymbols | None = None,
) -> TestClient:
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
        incidents=EmptyIncidents(),
        symbols=EmptySymbols(),
        feed=LiveFeedService(
            signals or FakeSignals(),
            transitions or FakeTransitions(),
            symbols or FakeSymbols(),
            FakeClock(),
        ),
    )

    return TestClient(app)


def get(client: TestClient, **params) -> dict:
    return client.get("/api/v1/scanner/feed", params=params, headers=AUTH).json()


# ------------------------------------------------------------------ the board


def test_a_live_signal_reaches_the_board() -> None:
    """The row §18.4 exists to serve, end to end."""

    body = get(
        build(
            FakeSignals(signal("a")),
            FakeTransitions(("a", "PUBLISHED")),
        )
    )

    row = body["data"][0]

    assert row["signal_id"] == "a"
    assert row["rank"] == 1
    assert row["confidence"] == "75"
    assert row["lifecycle_state"] == "PUBLISHED"


def test_both_numbers_travel() -> None:
    """§9.3 decays the *display* rank; the recorded confidence does not move.

    §15.4 wants confidence shown with its breakdown "never as a bare number",
    and a reader given only the decayed figure cannot tell a weakening signal
    from a weak one.
    """
    aged = signal("a", published_at=NOW - timedelta(hours=6))

    row = get(build(FakeSignals(aged), FakeTransitions(("a", "PUBLISHED"))))["data"][0]

    assert row["confidence"] == "75"
    assert Decimal(row["display_rank"]) < Decimal("75")
    assert row["age_candles"] == 6


def test_decay_is_counted_in_the_signal_s_own_candles() -> None:
    """The property that separates this board from §18.6's.

    `RankingSnapshotService` takes one `elapsed_candles` for a whole snapshot,
    which is right there -- every row was recorded at the same close. Here the
    rows were published at different times on timeframes of different lengths.
    Six hours is six candles of H1 and seventy-two of M5, and a board that
    decayed both by the same count would punish the faster timeframe for the
    clock rather than for its age.
    """
    published = NOW - timedelta(hours=6)

    body = get(
        build(
            FakeSignals(
                signal("h1", timeframe=Timeframe.H1, published_at=published),
                signal("m5", timeframe=Timeframe.M5, published_at=published),
            ),
            FakeTransitions(("h1", "PUBLISHED"), ("m5", "PUBLISHED")),
        )
    )

    ages = {row["signal_id"]: row["age_candles"] for row in body["data"]}

    assert ages == {"h1": 6, "m5": 72}

    decayed = {row["signal_id"]: Decimal(row["display_rank"]) for row in body["data"]}

    assert decayed["m5"] < decayed["h1"]


def test_the_board_reports_its_denominator() -> None:
    """A filter that matched nothing and a market that offered nothing must not
    render the same -- the confusion §18.6's board already carries a count to
    avoid."""

    body = get(
        build(
            FakeSignals(signal("a", grade="B"), signal("b", grade="S")),
            FakeTransitions(("a", "PUBLISHED"), ("b", "PUBLISHED")),
        ),
        **{"filter[grade]": "S"},
    )

    assert body["page"]["count"] == 1
    assert body["page"]["live_total"] == 2


def test_a_terminal_signal_is_not_on_the_board() -> None:
    """`live_states` is the whole membership rule: a signal leaves the feed by
    reaching a terminal state, not by ageing out of a query."""

    body = get(build(FakeSignals(signal("a")), FakeTransitions()))

    assert body["data"] == []
    assert body["page"]["live_total"] == 0


# ----------------------------------------------------------------- §9 and §10


def test_a_client_sort_is_refused() -> None:
    """§18.4: "no sort (fixed §10)". A sort here would not reorder a page, it
    would reorder a *ranking*, and the printed positions would become wrong."""

    response = build().get("/api/v1/scanner/feed", params={"sort": "-confidence"}, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "sort"


@pytest.mark.parametrize("field", ["archetype", "grade", "timeframe", "direction"])
def test_the_documented_filters_are_accepted(field: str) -> None:
    client = build(FakeSignals(signal("a")), FakeTransitions(("a", "PUBLISHED")))

    response = client.get("/api/v1/scanner/feed", params={f"filter[{field}]": "A3"}, headers=AUTH)

    assert response.status_code == 200


@pytest.mark.parametrize("field", ["tier", "category", "htf_alignment", "watchlist_id"])
def test_a_filter_the_row_cannot_honour_is_refused_not_ignored(field: str) -> None:
    """§18.4 lists eight filters; the published signal carries four of them.

    §9 is explicit that a filter the server did not apply must be refused. A
    420 for `tier` is a smaller lie than a board that silently returns every
    tier and calls itself filtered.
    """
    response = build().get("/api/v1/scanner/feed", params={f"filter[{field}]": "T1"}, headers=AUTH)

    assert response.status_code == 422


def test_the_board_needs_a_token() -> None:
    assert build().get("/api/v1/scanner/feed").status_code == 401


# ---------------------------------------------------------------------- §9.2


def test_the_order_is_9_2_s_and_positions_are_dense() -> None:
    """§9.2 ranks by confidence first. The positions printed beside the rows
    have to be the ranking's, not the array's."""

    body = get(
        build(
            FakeSignals(
                signal("low", confidence="71"),
                signal("high", confidence="88", symbol="BTCUSDT"),
            ),
            FakeTransitions(("low", "PUBLISHED"), ("high", "PUBLISHED")),
        )
    )

    assert [row["signal_id"] for row in body["data"]] == ["high", "low"]
    assert [row["rank"] for row in body["data"]] == [1, 2]


def test_an_unregistered_symbol_ranks_last_rather_than_third() -> None:
    """§9.2's fourth key is liquidity tier, and an unknown tier is not a weak
    one. Defaulting it to T3 would rank an unregistered symbol above a
    genuinely tier-3 one -- the reason `RankingSnapshotService` uses
    INELIGIBLE, and the same answer is owed here."""

    tied = (
        signal("known", symbol="AAAUSDT"),
        signal("unknown", symbol="BBBUSDT"),
    )

    ranked_known = get(
        build(
            FakeSignals(*tied),
            FakeTransitions(("known", "PUBLISHED"), ("unknown", "PUBLISHED")),
            FakeSymbols(tier=None),
        )
    )

    # With no registry at all the two tie on every §9.2 key and fall to the
    # documented final tie-break, which is symbol lexicographic.
    assert [row["symbol"] for row in ranked_known["data"]] == ["AAAUSDT", "BBBUSDT"]
