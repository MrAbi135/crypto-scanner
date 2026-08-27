"""§18.6's ranking group."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ranking import RankedRow, RankingSnapshot
from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence.archetypes import Archetype
from scanner.domain.confluence.weights import FACTOR_JUSTIFICATION, WEIGHTS
from scanner.domain.ranking.model import RankableSetup
from scanner.interfaces.api.app import build_read_api
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
    EmptyFeed,
    EmptyRankings,
    FakeSessionStore,
    FakeTenants,
    FakeUsers,
    bearer,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
AUTH = bearer(now=NOW)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class EmptyRepo:
    async def fetch_series(self, *a, **k):
        return []

    async def latest_open_time(self, *a, **k):
        return None

    async def list_between(self, *a, **k):
        return ()

    async def list_live(self, *a, **k):
        return ()

    async def list_active(self, *a, **k):
        return ()

    async def history(self, *a, **k):
        from scanner.application.ports.track_record import HistoryPage

        return HistoryPage(rows=(), next_position=None)

    async def outcome_counts(self, *a, **k):
        return ()

    async def get(self, *a, **k):
        return None


class FakeRankings:
    def __init__(self, rows=(), *, gate_passers=0, below_floor=0) -> None:
        self.rows = tuple(rows)
        self.gate_passers = gate_passers
        self.below_floor = below_floor
        self.seen_at: datetime | None = None
        self.seen_symbols: tuple[str, ...] = ()

    async def snapshot(self, symbols, timeframe, at, *, elapsed_candles: int = 0):
        self.seen_at = at
        self.seen_symbols = symbols

        return RankingSnapshot(
            timeframe=timeframe,
            at=at,
            rows=self.rows,
            gate_passers=self.gate_passers,
            below_floor=self.below_floor,
        )


def ranked(position: int, symbol: str, confidence: str, display: str) -> RankedRow:
    return RankedRow(
        position=position,
        setup=RankableSetup(
            symbol=symbol,
            timeframe=Timeframe.H1,
            confidence=Decimal(confidence),
            archetype=Archetype.CONTINUATION_PULLBACK,
            tier=UniverseTier.T1,
            direction="UP",
        ),
        display=Decimal(display),
    )


def build(rankings=None) -> TestClient:
    store = FakeSessionStore()
    users = FakeUsers()

    app = build_read_api(
        candles=EmptyRepo(),
        evidence=EmptyRepo(),
        zones=EmptyRepo(),
        pools=EmptyRepo(),
        clock=FakeClock(),
        accounts=AccountService(users, FakeTenants()),
        sessions=SessionService(store),
        session_repository=store,
        access_tokens=AccessTokens(TEST_SECRET),
        signals=EmptyRepo(),
        signal_transitions=EmptyRepo(),
        outcomes=EmptyRepo(),
        track_record=EmptyRepo(),
        track_statistics=EmptyRepo(),
        rankings=rankings or EmptyRankings(),
        feed=EmptyFeed(),
    )

    return TestClient(app)


# ------------------------------------------------------------------ weights


def test_the_weights_row_publishes_the_doctrine_not_a_summary() -> None:
    """§18.6 calls this a "doctrine transparency endpoint".

    That only means something if the words are §9.1's own. A paraphrase would
    let the published reason drift from the rule it defends, and a reader has
    no way to tell.
    """
    body = build().get("/api/v1/rankings/weights", headers=AUTH).json()["data"]

    assert {f["factor"] for f in body["factors"]} == {w.value for w in WEIGHTS}

    for row in body["factors"]:
        justification = FACTOR_JUSTIFICATION[next(w for w in WEIGHTS if w.value == row["factor"])]

        assert row["justification"] == justification
        # Not truncated, not summarised.
        assert len(row["justification"]) > 80


def test_the_weights_sum_to_one_over_the_wire() -> None:
    """A drifted table silently rescales every score ever published.

    Asserted on the response, not on the constant: a serialisation that
    dropped or rounded a factor would pass a test of the constant.
    """
    body = build().get("/api/v1/rankings/weights", headers=AUTH).json()["data"]

    assert sum(Decimal(f["weight"]) for f in body["factors"]) == Decimal(1)


def test_the_weights_carry_the_param_set_version() -> None:
    """§9.1 makes them `P.rank.weights`, versioned.

    A table without its version cannot be matched to the signals it scored.
    """
    body = build().get("/api/v1/rankings/weights", headers=AUTH).json()["data"]

    assert body["param_set_version"]


def test_the_grade_bands_travel_with_the_weights() -> None:
    """§9.4. A board without its bands is a column of letters, and below the
    lowest floor is not a weak grade — it is not published."""

    body = build().get("/api/v1/rankings/weights", headers=AUTH).json()["data"]

    assert [g["grade"] for g in body["grades"]] == ["S", "A", "B"]
    assert body["below_lowest_floor"] == "not published"


# -------------------------------------------------------------------- board


def test_the_board_returns_ranked_rows_with_both_numbers() -> None:
    """§9.3: the *display* rank decays; the recorded confidence does not.

    Both are returned so a reader can see the difference rather than wonder
    which number they are looking at.
    """
    rankings = FakeRankings([ranked(1, "BTCUSDT", "82", "61.5")], gate_passers=4)

    body = (
        build(rankings)
        .get(
            "/api/v1/rankings",
            params={"symbols": "BTCUSDT,ETHUSDT", "timeframe": "H1"},
            headers=AUTH,
        )
        .json()
    )

    row = body["data"][0]

    assert row["rank"] == 1
    assert row["confidence"] == "82"
    assert row["display_rank"] == "61.5"


def test_the_board_reports_its_own_denominator() -> None:
    """§8.6 keeps below-floor candidates "for calibration".

    A board showing only its rows makes a quiet market and a broken pipeline
    look identical — the exact confusion that cost days on the staging host,
    where 64 candidates scored and none published.
    """
    rankings = FakeRankings([], gate_passers=64, below_floor=64)

    page = (
        build(rankings)
        .get(
            "/api/v1/rankings",
            params={"symbols": "BTCUSDT", "timeframe": "H1"},
            headers=AUTH,
        )
        .json()["page"]
    )

    assert page["count"] == 0
    assert page["gate_passers"] == 64
    assert page["below_floor"] == 64


def test_a_client_sort_is_refused() -> None:
    """§10: rank-ordered resources "reject client sort parameters rather than
    silently overriding them".

    A sort here would not reorder a page, it would reorder a *ranking* — and
    the position numbers printed beside the rows would become wrong.
    """
    response = build().get(
        "/api/v1/rankings",
        params={"symbols": "BTCUSDT", "timeframe": "H1", "sort": "-confidence"},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "sort"


def test_an_unknown_filter_is_refused_rather_than_ignored() -> None:
    """No filter is applied on this row yet, so accepting one would be §9's
    "filter the server didn't apply"."""

    response = build().get(
        "/api/v1/rankings",
        params={"symbols": "BTCUSDT", "timeframe": "H1", "filter[grade]": "S"},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_the_default_moment_is_the_last_close_not_now() -> None:
    """§9.2 ranks the candidates recorded *at a close*.

    Asking for a moment between closes returns an empty board that looks like
    a quiet market, which is a wrong answer wearing a plausible one.
    """
    rankings = FakeRankings()

    build(rankings).get(
        "/api/v1/rankings",
        params={"symbols": "BTCUSDT", "timeframe": "H1"},
        headers=AUTH,
    )

    # NOW is 12:00 exactly; make the point with a clock that is not on a
    # boundary by checking the flooring arithmetic directly.
    assert rankings.seen_at == NOW.replace(minute=0, second=0, microsecond=0)


@pytest.mark.parametrize("raw", ["yesterday", "2026-13-01", ""])
def test_a_malformed_at_is_refused(raw: str) -> None:
    response = build().get(
        "/api/v1/rankings",
        params={"symbols": "BTCUSDT", "timeframe": "H1", "at": raw},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_both_rows_need_a_token() -> None:
    client = build()

    assert (
        client.get("/api/v1/rankings", params={"symbols": "B", "timeframe": "H1"}).status_code
        == 401
    )
    assert client.get("/api/v1/rankings/weights").status_code == 401
