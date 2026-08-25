"""The candles row, driven through a real FastAPI app (API Spec §18.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from scanner.interfaces.api.app import IMPLEMENTED_ROWS, build_read_api
from scanner.shared import Timeframe
from tests.support.builders import make_candle
from tests.unit.interfaces.api.identity_fixtures import bearer, identity

# Every read row is authenticated as of S10-minimal. Minted once at module
# scope: driving `/auth/login` in each test would make every read test also
# a test of Argon2.
NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
AUTH = bearer(now=NOW)


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
        **identity(),
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


def test_a_read_row_without_a_token_is_refused() -> None:
    """What the removed `allow_unauthenticated` tripwire was standing in for.

    That flag existed because these rows are 🔑 in the spec and identity did
    not exist. It has been deleted, and this is the assertion that has to
    replace it — otherwise removing the tripwire removes the only thing
    checking anything.
    """
    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    # RFC 6750: a bearer-protected resource says so on the 401.
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic abc",
        "Bearer not-a-jwt",
        "Bearer a.b.c",
    ],
)
def test_a_malformed_or_unsigned_token_is_refused(header: str) -> None:
    """One answer for every way of failing.

    Expired, wrong signature, wrong scheme, absent: a caller who can tell them
    apart can probe the token format.
    """
    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
        headers={"Authorization": header},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_a_token_signed_with_another_secret_is_refused() -> None:
    """The signature is checked, not merely the shape.

    A verifier that decoded without verifying would pass every test above —
    those tokens are malformed. This one is a structurally perfect JWT with
    the right claims and the wrong key.
    """
    from scanner.application.identity.tokens import AccessTokens

    forged = AccessTokens("a-different-secret-of-quite-sufficient-length").mint(
        user_id="test-user",
        tenant_id="default",
        session_id="test-session",
        role="user",
        now=NOW,
    )

    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
        headers={"Authorization": f"Bearer {forged}"},
    )

    assert response.status_code == 401


def test_an_expired_token_is_refused() -> None:
    """TAD §20 caps the access token at fifteen minutes, and it has to bind."""
    from datetime import timedelta

    from tests.unit.interfaces.api.identity_fixtures import bearer

    stale = bearer(now=NOW - timedelta(hours=2))

    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
        headers=stale,
    )

    assert response.status_code == 401


def test_a_window_comes_back_in_the_success_envelope() -> None:
    client, _, _ = build(series(3))

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1"},
        headers=AUTH,
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
        headers=AUTH,
    ).json()["data"][0]

    for field in ("open", "high", "low", "close", "volume"):
        assert isinstance(row[field], str), field


def test_the_symbol_is_normalised_before_the_query() -> None:
    client, repo, _ = build()

    client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "btcusdt", "timeframe": "h1"},
        headers=AUTH,
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
        headers=AUTH,
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
        headers=AUTH,
    )

    _, _, _, end = repo.calls[0]

    assert end == NOW


def test_the_window_is_limit_candles_back_from_the_anchor() -> None:
    client, repo, _ = build()

    client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1", "limit": 200},
        headers=AUTH,
    )

    _, _, start, end = repo.calls[0]

    assert end == NOW
    assert start == NOW - Timeframe.H1.duration * 200


def test_an_unknown_timeframe_is_a_field_precise_validation_error() -> None:
    client, _, _ = build()

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "M3"},
        headers=AUTH,
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
        headers={**AUTH, "X-Correlation-Id": "01CLIENTSUPPLIED"},
    )

    assert response.json()["error"]["correlation_id"] == "01CLIENTSUPPLIED"


def test_a_limit_beyond_the_documented_maximum_is_refused() -> None:
    client, _, _ = build()

    response = client.get(
        "/api/v1/market/candles",
        params={"symbol_id": "BTCUSDT", "timeframe": "H1", "limit": 5000},
        headers=AUTH,
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


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_the_read_api_mounts_in_every_environment_now_that_it_authenticates(
    env: str,
) -> None:
    """The production bail-out is gone, and this replaces it.

    It existed because these rows had no authentication and an exposed read
    API would have handed the whole detection record to anyone who found the
    port. They authenticate now, so refusing to mount in production would be
    refusing to ship.

    What is still absent is TAD §21's entitlement layer — a limit on *what* a
    known caller may see. `ENTITLEMENTS_ENFORCED` says so, and is asserted
    below rather than left as a comment, so the check that lands with plans
    has something to flip.
    """
    from scanner.config.processes import ApiSettings
    from scanner.interfaces.api.app import ENTITLEMENTS_ENFORCED
    from scanner.runtime.api import build_api_app

    settings = ApiSettings(
        env=env,
        db_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
        access_token_secret="a-test-signing-secret-of-sufficient-length",
    )

    app = build_api_app(settings)

    assert any(getattr(route, "path", "") == "" for route in app.routes)
    assert ENTITLEMENTS_ENFORCED is False


def test_the_api_refuses_to_start_without_a_signing_secret() -> None:
    """No default, not even an empty string.

    A default would let the process boot and issue tokens anyone could forge,
    and the symptom — everything works — is indistinguishable from correct
    operation.
    """
    import pydantic

    from scanner.config.processes import ApiSettings

    with pytest.raises(pydantic.ValidationError, match="access_token_secret"):
        ApiSettings(
            env="prod",
            db_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
            redis_url="redis://localhost:6379/0",
        )


def test_a_short_signing_secret_is_refused() -> None:
    """Below 32 characters, signing is theatre."""
    import pydantic

    from scanner.config.processes import ApiSettings

    with pytest.raises(pydantic.ValidationError):
        ApiSettings(
            env="prod",
            db_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            access_token_secret="too-short",
        )


# §18.1's own rows: the way in cannot require what it issues. Anything not
# listed here must refuse an anonymous request.
OPEN_ROWS = {
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    # FastAPI's own documentation routes. They describe the surface and serve
    # no data, and the docs UI fetches the schema from the browser where it
    # cannot attach a bearer token — so protecting them would mean the docs
    # simply do not load. Listed as a decision rather than left to fall
    # through: if the schema itself becomes sensitive, this is the line to
    # remove.
    "GET /api/v1/docs",
    "GET /api/v1/docs/oauth2-redirect",
    "GET /api/v1/openapi.json",
    "GET /api/v1/redoc",
}


def test_every_non_auth_route_requires_a_token() -> None:
    """TAD §21: "a route without a policy declaration fails CI".

    Enumerated from the built app rather than from a list someone maintains.
    The protection is declared per-router, which is forgettable exactly once —
    when a third router is added and included without `dependencies`. This is
    the assertion that notices.

    It is also the check that would have caught the first version of this
    change, where the auth router and the bearer dependency both existed and
    neither was applied to the read rows: every one of them still answered
    200 to an anonymous request.
    """
    client, _, app = build(series(3))

    unprotected = []

    for route in app.routes:
        path = getattr(route, "path", "")

        if not path.startswith("/api/v1/"):
            continue

        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            row = f"{method} {path}"

            if row in OPEN_ROWS:
                continue

            # A path parameter needs *something* in it to route at all; the
            # value is irrelevant because auth is refused before the handler.
            probe = path.replace("{symbol_id}", "BTCUSDT").replace("{session_id}", "any")

            response = client.request(method, probe)

            if response.status_code != 401:
                unprotected.append(f"{row} -> {response.status_code}")

    assert unprotected == []
