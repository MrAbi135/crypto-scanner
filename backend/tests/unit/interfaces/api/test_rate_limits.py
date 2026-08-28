"""§11's rate limiting policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.interfaces.api.app import IMPLEMENTED_ROWS, build_read_api
from scanner.interfaces.api.ratelimit import (
    FREE_PER_MINUTE,
    RATE_LIMIT_CLASS,
    InMemoryRateLimitStore,
    assert_every_row_has_a_class,
)
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

START = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


class MovableClock:
    """§11 measures a window, so the tests have to be able to move through one.

    Injected rather than patched: the identity layer once verified expiry
    against the system clock while everything around it took an injected `now`,
    and its tests moved a clock the code never read.
    """

    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at

    def advance(self, **kwargs) -> None:
        self.at += timedelta(**kwargs)


def build(clock: MovableClock | None = None) -> tuple[TestClient, MovableClock]:
    moving = clock or MovableClock()
    store = FakeSessionStore()

    app = build_read_api(
        candles=EmptySignals(),
        evidence=EmptySignals(),
        zones=EmptySignals(),
        pools=EmptySignals(),
        clock=moving,
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
        symbols=EmptySymbols(),
        rate_limits=InMemoryRateLimitStore(),
    )

    return TestClient(app), moving


# The lightest authenticated row, so the limit under test is the class's and
# not some collaborator's.
LIGHT = "/api/v1/rankings/weights"
HEAVY = "/api/v1/rankings"
HEAVY_PARAMS = {"symbols": "BTCUSDT", "timeframe": "H1"}


def auth(clock: MovableClock) -> dict[str, str]:
    return bearer(now=clock.at)


# ----------------------------------------------------------------- the budget


def test_the_budget_is_reported_on_a_served_response() -> None:
    """§11: "every response carries X-RateLimit-Limit/Remaining/Reset".

    Every response, not only the refused one. A client that can only learn its
    budget by exceeding it has to exceed it.
    """
    client, clock = build()

    response = client.get(LIGHT, headers=auth(clock))

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == str(FREE_PER_MINUTE["read:light"])
    assert response.headers["X-RateLimit-Remaining"] == str(FREE_PER_MINUTE["read:light"] - 1)
    assert int(response.headers["X-RateLimit-Reset"]) == 60


def test_the_budget_is_reported_on_a_refused_one_too() -> None:
    """§11 says *every* response, and a 401 is a response.

    The dependency sets these on the injected `Response`, which FastAPI carries
    onto a served reply and nowhere else -- an endpoint that raises is rendered
    by the exception handler from a fresh response. Asserting only the 200 path
    left that hole open, and it was open on the host: an unauthenticated call
    to a live endpoint came back with no budget headers at all.
    """
    client, _ = build()

    unauthenticated = client.get(LIGHT)

    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["X-RateLimit-Limit"] == str(FREE_PER_MINUTE["read:light"])
    # Charged, not merely reported: an unauthenticated flood must cost its
    # sender something.
    assert (
        int(unauthenticated.headers["X-RateLimit-Remaining"]) == FREE_PER_MINUTE["read:light"] - 1
    )


def test_a_refusal_keeps_the_authenticate_header_it_was_given() -> None:
    """RFC 6750 requires `WWW-Authenticate` on a 401 from a bearer-protected
    resource. Merging the budget in must not displace it."""

    client, _ = build()

    unauthenticated = client.get(LIGHT)

    assert "WWW-Authenticate" in unauthenticated.headers
    assert "X-RateLimit-Limit" in unauthenticated.headers


def test_the_budget_counts_down() -> None:
    client, clock = build()

    first = client.get(LIGHT, headers=auth(clock))
    second = client.get(LIGHT, headers=auth(clock))

    assert int(first.headers["X-RateLimit-Remaining"]) == FREE_PER_MINUTE["read:light"] - 1
    assert int(second.headers["X-RateLimit-Remaining"]) == FREE_PER_MINUTE["read:light"] - 2


def test_the_limit_refuses_with_a_retry_after() -> None:
    """§11: "429 always includes retry_after"."""

    client, clock = build()
    headers = auth(clock)

    for _ in range(FREE_PER_MINUTE["read:heavy"]):
        assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 200

    refused = client.get(HEAVY, params=HEAVY_PARAMS, headers=headers)

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "RATE_LIMITED"
    assert refused.json()["error"]["retry_after"] >= 1
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.headers["X-RateLimit-Remaining"] == "0"


def test_a_refusal_does_not_push_the_window_out() -> None:
    """The failure where being rate limited becomes permanent.

    A client that polls through a closed window would keep resetting it if a
    refused request counted, and the window would never reopen.
    """
    client, clock = build()
    headers = auth(clock)

    for _ in range(FREE_PER_MINUTE["read:heavy"]):
        client.get(HEAVY, params=HEAVY_PARAMS, headers=headers)

    clock.advance(seconds=30)

    for _ in range(10):
        assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 429

    # 31 s in; the window closes at 60 and the polling must not have moved it.
    clock.advance(seconds=30)

    assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 200


def test_the_window_reopens() -> None:
    client, clock = build()
    headers = auth(clock)

    for _ in range(FREE_PER_MINUTE["read:heavy"]):
        client.get(HEAVY, params=HEAVY_PARAMS, headers=headers)

    assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 429

    clock.advance(minutes=1)

    assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 200


# ------------------------------------------------------------------- the keys


def test_the_classes_hold_separate_budgets() -> None:
    """§11 gives each class its own row: different numbers for different costs.

    Asserted on the light budget's *remaining*, not on it still answering 200.
    Thirty heavy requests against a shared bucket would leave ninety of the
    light hundred and twenty, so the weaker assertion passes with the two
    classes sharing one counter -- which is the mistake worth catching.
    """
    client, clock = build()
    headers = auth(clock)

    for _ in range(FREE_PER_MINUTE["read:heavy"]):
        client.get(HEAVY, params=HEAVY_PARAMS, headers=headers)

    assert client.get(HEAVY, params=HEAVY_PARAMS, headers=headers).status_code == 429

    light = client.get(LIGHT, headers=headers)

    assert light.status_code == 200
    # Untouched by the heavy spend: this is its first request.
    assert int(light.headers["X-RateLimit-Remaining"]) == FREE_PER_MINUTE["read:light"] - 1


def test_the_template_is_the_key_not_the_path() -> None:
    """A limiter keyed on the literal path would give every signal id its own
    budget, which is no limit at all."""

    client, clock = build()
    headers = auth(clock)

    for index in range(FREE_PER_MINUTE["read:light"]):
        client.get(f"/api/v1/signals/id-{index}", headers=headers)

    refused = client.get("/api/v1/signals/one-more", headers=headers)

    assert refused.status_code == 429


def test_an_anonymous_caller_is_keyed_and_still_limited() -> None:
    """§11: "per user (authenticated) or IP (anonymous)". A 401 costs budget --
    otherwise an unauthenticated flood is free."""

    client, _ = build()

    for _ in range(FREE_PER_MINUTE["read:light"]):
        assert client.get(LIGHT).status_code == 401

    assert client.get(LIGHT).status_code == 429


# ------------------------------------------------------------------ the table


def test_every_implemented_row_is_classed() -> None:
    """Called at build time too. An endpoint added without deciding its class
    would be served unlimited, and unlimited is the one answer §11 does not
    offer."""

    assert_every_row_has_a_class(IMPLEMENTED_ROWS)

    assert set(RATE_LIMIT_CLASS) == set(IMPLEMENTED_ROWS)


def test_an_unclassed_row_refuses_the_build() -> None:
    with pytest.raises(ValueError, match="no rate-limit class"):
        assert_every_row_has_a_class((*IMPLEMENTED_ROWS, "GET /api/v1/invented"))


def test_a_class_left_behind_for_a_deleted_row_refuses_the_build() -> None:
    """The reverse direction. A class for a row that was renamed would limit
    nothing while looking like coverage."""

    with pytest.raises(ValueError, match="do not exist"):
        assert_every_row_has_a_class(IMPLEMENTED_ROWS[1:])


def test_every_class_used_has_a_limit() -> None:
    assert set(RATE_LIMIT_CLASS.values()) <= set(FREE_PER_MINUTE)


# ------------------------------------------------------------------ the store


def test_expired_windows_are_forgettable() -> None:
    """An IP-keyed anonymous bucket grows with every distinct caller ever seen,
    which is unbounded by design."""

    store = InMemoryRateLimitStore()

    store.take("ip:1.2.3.4|read:light", limit=5, now=START)
    store.take("ip:5.6.7.8|read:light", limit=5, now=START)

    assert store.forget_expired(START) == 0
    assert store.forget_expired(START + timedelta(minutes=2)) == 2
