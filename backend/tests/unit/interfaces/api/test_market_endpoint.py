"""The candles row, driven through a real FastAPI app (API Spec §18.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from scanner.interfaces.api.app import IMPLEMENTED_ROWS, build_read_api
from scanner.shared import Timeframe
from tests.support.builders import make_candle

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class EmptyRepo:
    """Stands in for the doctrine repositories this module does not exercise."""

    async def list_structure(self, *args):
        return ()

    async def list_liquidity(self, *args):
        return ()

    async def list_live(self, *args):
        return ()

    async def list_active(self, *args):
        return ()


class FakeCandleRepository:
    def __init__(self, series=()) -> None:
        self.series = list(series)
        self.calls: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def fetch_series(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return self.series

    async def latest_open_time(self, symbol, timeframe):
        return self.series[-1].open_time if self.series else None


def build(series=()):
    repo = FakeCandleRepository(series)

    app = build_read_api(
        candles=repo,
        evidence=EmptyRepo(),
        zones=EmptyRepo(),
        pools=EmptyRepo(),
        clock=FakeClock(),
        allow_unauthenticated=True,
    )

    return TestClient(app), repo, app


def series(count: int, timeframe: Timeframe = Timeframe.H1):
    return [
        make_candle(
            symbol="BTCUSDT",
            timeframe=timeframe,
            open_time=NOW - timeframe.duration * (count - i),
        )
        for i in range(count)
    ]


def test_the_api_refuses_to_build_without_an_explicit_private_declaration() -> None:
    """The rows are 🔑 in the spec and identity does not exist yet.

    A placeholder auth that "works" is the kind of thing that survives to
    production. Refusing to construct is louder and cannot be forgotten.
    """
    with pytest.raises(RuntimeError, match="no authentication"):
        build_read_api(
            candles=FakeCandleRepository(),
            evidence=EmptyRepo(),
            zones=EmptyRepo(),
            pools=EmptyRepo(),
            clock=FakeClock(),
            allow_unauthenticated=False,
        )


def test_a_window_comes_back_in_the_success_envelope() -> None:
    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
    )

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {"data", "meta", "page"}
    assert body["meta"]["generated_at"] == NOW.isoformat()
    assert body["meta"]["freshness"]["state"] == "RECORDED"
    assert body["page"] == {"count": 3, "has_more": False}
    assert len(body["data"]) == 3


def test_prices_are_strings_on_the_wire() -> None:
    """API §5, asserted on the actual HTTP body rather than on the envelope."""
    client, _, _ = build(series(1))

    row = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
    ).json()["data"][0]

    for field in ("open", "high", "low", "close", "volume"):
        assert isinstance(row[field], str), field


def test_the_symbol_is_normalised_before_the_query() -> None:
    client, repo, _ = build()

    client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "btcusdt", "timeframe": "h1"},
    )

    symbol, timeframe, _, _ = repo.calls[0]

    assert symbol == "BTCUSDT"
    assert timeframe is Timeframe.H1


def test_a_historical_context_anchors_to_its_own_newest_candle() -> None:
    """The defect that hid twelve golden datasets.

    Anchoring to `clock.now()` looks correct for a live symbol, because its
    newest candle is roughly now. For anything historical the window lands in
    empty space and the chart says "no candles for this context yet" -- a
    plausible sentence about data that is sitting in the table.
    """
    january = datetime(2026, 1, 5, tzinfo=UTC)

    old = [
        make_candle(
            symbol="GOLDENFVG",
            timeframe=Timeframe.H1,
            open_time=january + Timeframe.H1.duration * i,
        )
        for i in range(6)
    ]

    client, repo, _ = build(old)

    body = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "GOLDENFVG", "timeframe": "H1", "limit": 100},
    ).json()

    _, _, start, end = repo.calls[0]

    # One step past the newest stored candle, so that candle is inside the
    # half-open window rather than on its excluded edge.
    assert end == old[-1].open_time + Timeframe.H1.duration
    assert start == end - Timeframe.H1.duration * 100
    assert len(body["data"]) == 6


def test_an_unknown_context_still_falls_back_to_the_clock() -> None:
    client, repo, _ = build()

    client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "NOSUCH", "timeframe": "H1"},
    )

    _, _, _, end = repo.calls[0]

    assert end == NOW


def test_the_window_is_limit_candles_back_from_the_anchor() -> None:
    client, repo, _ = build()

    client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1", "limit": 200},
    )

    _, _, start, end = repo.calls[0]

    assert end == NOW
    assert start == NOW - Timeframe.H1.duration * 200


def test_an_unknown_timeframe_is_a_field_precise_validation_error() -> None:
    client, _, _ = build()

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "M3"},
    )

    assert response.status_code == 400

    body = response.json()["error"]

    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"][0]["field"] == "timeframe"
    assert body["correlation_id"]


def test_a_supplied_correlation_id_is_echoed_back() -> None:
    """A trace that changes id at the boundary is two traces."""
    client, _, _ = build()

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "M3"},
        headers={"X-Correlation-Id": "01CLIENTSUPPLIED"},
    )

    assert response.json()["error"]["correlation_id"] == "01CLIENTSUPPLIED"


def test_a_limit_beyond_the_documented_maximum_is_refused() -> None:
    client, _, _ = build()

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1", "limit": 5000},
    )

    assert response.status_code == 422


def test_only_the_declared_subset_is_mounted() -> None:
    """The spec being frozen forbids inventing rows that are not in it.

    Asserted against the generated OpenAPI document rather than `app.routes`:
    this FastAPI version wraps included routers in `_IncludedRouter` instead of
    flattening them, so `app.routes` does not list them. OpenAPI is the better
    assertion anyway -- it is what a client actually sees, and it is the
    artefact the S11 contract suite will diff against the spec.
    """
    _, _, app = build()

    mounted = {
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    assert mounted == set(IMPLEMENTED_ROWS)


@pytest.mark.parametrize(
    ("env", "should_mount"),
    [("dev", True), ("staging", True), ("prod", False)],
)
def test_the_read_api_is_never_mounted_in_production(env: str, should_mount: bool) -> None:
    """An unauthenticated read API in production exposes the whole record.

    Pinned across every valid env value because the first version of this
    guard compared against "production", which `BaseProcessSettings` does not
    permit -- the branch could never fire, and the API would have mounted in
    production while the code looked correct.
    """
    from scanner.config.processes import ApiSettings
    from scanner.runtime.api import build_api_app

    settings = ApiSettings(
        env=env,
        db_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
    )

    app = build_api_app(settings)

    mounted = any(getattr(route, "path", "") == "" for route in app.routes)

    assert mounted is should_mount
