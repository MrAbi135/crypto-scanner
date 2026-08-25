"""§18.1's rows, driven through a real FastAPI app."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService, hash_password
from scanner.application.identity.tokens import ACCESS_TTL, AccessTokens
from scanner.application.ports.identity import UserRecord
from scanner.interfaces.api.app import build_read_api
from scanner.interfaces.api.auth import REFRESH_COOKIE
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
    FakeSessionStore,
    FakeTenants,
    FakeUsers,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


class FakeClock:
    """Advanceable, because rotation and expiry are both about time passing."""

    def __init__(self) -> None:
        self.at = NOW

    def now(self) -> datetime:
        return self.at


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


def build() -> tuple[TestClient, FakeClock, FakeSessionStore, FakeUsers]:
    clock = FakeClock()
    store = FakeSessionStore()

    users = FakeUsers(
        [
            UserRecord(
                user_id="u-1",
                tenant_id="default",
                email="ops@example.com",
                password_hash=hash_password(PASSWORD),
                role="user",
                status="ACTIVE",
                created_at=NOW,
            )
        ]
    )

    app = build_read_api(
        candles=EmptyRepo(),
        evidence=EmptyRepo(),
        zones=EmptyRepo(),
        pools=EmptyRepo(),
        clock=clock,
        accounts=AccountService(users, FakeTenants()),
        sessions=SessionService(store),
        session_repository=store,
        access_tokens=AccessTokens(TEST_SECRET),
    )

    return TestClient(app), clock, store, users


def login(client: TestClient, *, password: str = PASSWORD, email: str = "ops@example.com"):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def test_login_returns_an_access_token_and_sets_the_refresh_cookie() -> None:
    client, _, store, _ = build()

    response = login(client)

    assert response.status_code == 200

    body = response.json()

    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == int(ACCESS_TTL.total_seconds())
    assert body["user_id"] == "u-1"

    # TAD §20: the refresh token is a cookie, not a body field. A value the
    # page's JavaScript can read is one an injected script can exfiltrate.
    assert REFRESH_COOKIE in response.cookies
    assert "refresh" not in body

    cookie = next(c for c in response.headers.get_list("set-cookie") if REFRESH_COOKIE in c)

    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie.replace("samesite", "SameSite")
    assert "Path=/api/v1/auth" in cookie

    assert len(store.rows) == 1


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("ops@example.com", "the-wrong-password"),
        ("nobody@example.com", PASSWORD),
    ],
)
def test_bad_credentials_and_an_unknown_address_answer_identically(
    email: str, password: str
) -> None:
    """§18.1: `AUTH_REQUIRED` — "deliberately same code, no user-enumeration"."""

    client, _, _, _ = build()

    response = login(client, email=email, password=password)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert REFRESH_COOKIE not in response.cookies


def test_a_disabled_account_is_refused_the_same_way() -> None:
    from dataclasses import replace

    client, _, _, users = build()

    users.rows["u-1"] = replace(users.rows["u-1"], status="DISABLED")

    response = login(client)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_refresh_rotates_the_cookie_and_issues_a_new_access_token() -> None:
    client, clock, _, _ = build()

    login(client)

    first = client.cookies[REFRESH_COOKIE]

    clock.at = NOW.replace(hour=13)

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert client.cookies[REFRESH_COOKIE] != first


def test_replaying_a_superseded_refresh_cookie_revokes_the_family() -> None:
    """TAD §20's reuse path, end to end.

    §18.1 gives this its own code — `TOKEN_REVOKED` — because the client must
    stop retrying rather than treat it as an ordinary expiry.
    """
    client, clock, store, _ = build()

    login(client)

    stolen = client.cookies[REFRESH_COOKIE]

    clock.at = NOW.replace(hour=13)

    assert client.post("/api/v1/auth/refresh").status_code == 200

    # The thief presents the original.
    client.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_REVOKED"

    assert all(row.revoked for row in store.rows.values())

    # And the server clears the cookie, so the client stops replaying a dead
    # token against an already-revoked family — each replay would otherwise be
    # another alarm about a family that is already gone.
    #
    # Asserted on the response's `Set-Cookie` rather than on the client jar:
    # the manual `cookies.set` above leaves httpx holding two entries under
    # one name, which is an artefact of the test client and not of the server.
    cleared = next(c for c in response.headers.get_list("set-cookie") if REFRESH_COOKIE in c)

    assert 'scanner_refresh=""' in cleared or "scanner_refresh=;" in cleared
    assert "Max-Age=0" in cleared or "expires=" in cleared.lower()


def test_refresh_without_a_cookie_is_refused() -> None:
    client, _, _, _ = build()

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 401


def test_refresh_refuses_once_the_account_is_disabled() -> None:
    """A session must not outlive the account by its thirty-day refresh TTL.

    Checked on refresh as well as on login: credentials are verified once, and
    a family that only ever checked them at the start would keep rotating for
    a month after the account was turned off.
    """
    from dataclasses import replace

    client, clock, store, users = build()

    login(client)

    users.rows["u-1"] = replace(users.rows["u-1"], status="DISABLED")

    clock.at = NOW.replace(hour=13)

    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert all(row.revoked for row in store.rows.values())


def test_logout_ends_the_family_and_is_204_even_without_a_cookie() -> None:
    """A logout that can fail leaves the client believing it is still signed in."""

    client, _, store, _ = build()

    login(client)

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert all(row.revoked for row in store.rows.values())

    # Second time, with the cookie already cleared.
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_the_session_list_marks_the_callers_own_row() -> None:
    """Without `current`, the list is opaque ids and "revoke the other one" is
    a guess."""

    client, _, store, _ = build()

    body = login(client).json()
    token = body["access_token"]

    session_id = next(iter(store.rows))

    response = client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    rows = response.json()

    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id
    assert rows[0]["current"] is True


def test_revoking_another_users_session_is_a_404_not_a_403() -> None:
    """Ownership is checked before existence is admitted.

    A 403 would confirm the id names a real session belonging to someone
    else, which turns this row into an enumeration oracle.
    """
    client, _, store, _ = build()

    token = login(client).json()["access_token"]

    from dataclasses import replace

    theirs = replace(
        next(iter(store.rows.values())),
        session_id="someone-elses",
        user_id="u-2",
        refresh_hash="f" * 64,
    )
    store.rows["someone-elses"] = theirs

    auth = {"Authorization": f"Bearer {token}"}

    assert client.delete("/api/v1/auth/sessions/someone-elses", headers=auth).status_code == 404
    assert client.delete("/api/v1/auth/sessions/never-existed", headers=auth).status_code == 404
    # Untouched.
    assert not store.rows["someone-elses"].revoked


def test_revoking_your_own_session_works_and_drops_it_from_the_list() -> None:
    client, _, store, _ = build()

    token = login(client).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    session_id = next(iter(store.rows))

    assert client.delete(f"/api/v1/auth/sessions/{session_id}", headers=auth).status_code == 204
    assert client.get("/api/v1/auth/sessions", headers=auth).json() == []


def test_the_profile_row_is_the_bootstrap_call() -> None:
    """§18.2 `GET /me` — the first request S13 makes after login."""

    client, _, _, _ = build()

    token = login(client).json()["access_token"]

    body = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert body["user_id"] == "u-1"
    assert body["tenant_id"] == "default"
    assert body["email"] == "ops@example.com"
    assert body["role"] == "user"

    # TAD §21's entitlement layer is not built. Said in the payload rather
    # than left for a client to infer from a missing plan.
    assert body["entitlements_enforced"] is False

    # And absent, not empty: an empty capability list reads as "no
    # capabilities", which a client would correctly render as a locked
    # interface. A missing field is a question; an empty one is a wrong answer.
    assert "capabilities" not in body
    assert "plan" not in body


def test_the_profile_is_read_back_rather_than_reflected_from_the_token() -> None:
    """The token is a snapshot from up to fifteen minutes ago.

    Showing a stale email after a change would be a small lie that is hard to
    notice, so the row comes from the database.
    """
    from dataclasses import replace

    client, _, _, users = build()

    token = login(client).json()["access_token"]

    users.rows["u-1"] = replace(users.rows["u-1"], email="moved@example.com")

    body = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert body["email"] == "moved@example.com"


def test_a_token_for_a_deleted_account_gets_a_404_not_a_profile() -> None:
    """An access token outlives a deletion by up to its fifteen minutes."""

    from dataclasses import replace

    client, _, _, users = build()

    token = login(client).json()["access_token"]

    users.rows["u-1"] = replace(users.rows["u-1"], deleted_at=NOW)

    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_the_profile_row_needs_a_token() -> None:
    client, _, _, _ = build()

    assert client.get("/api/v1/me").status_code == 401
