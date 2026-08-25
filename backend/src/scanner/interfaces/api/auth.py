"""§18.1's authentication group, for the rows S10-minimal implements.

Implemented: login, refresh, logout, session list, session revoke.

Deferred, each for a stated reason rather than for want of time:

* `register`, `verify-email`, `password-reset/*` — every one "triggers
  verification email"; there is no transactional email provider chosen for
  this deployment. Accounts are provisioned with `scanner users create`.
* `totp/*` — no second factor before there is a first user asking for one.
* `ws-ticket` — §18.1 already defers it to S12, with the gateway that
  consumes it.
* `login-history` — reads DDD T38's audit log, which does not exist yet. An
  endpoint over a table with no writer cannot pass an honest contract test.

**The refresh token is an httpOnly cookie, per TAD §20.** Not a JSON field: a
value the page's JavaScript can read is a value an injected script can
exfiltrate, and the refresh token is the long-lived half of the pair. The
access token goes in the body precisely because it is short-lived and the
client must attach it to headers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from scanner.application.identity import (
    REFRESH_TTL,
    AccountService,
    RefreshOutcome,
    SessionService,
)
from scanner.application.identity.tokens import ACCESS_TTL, AccessTokens
from scanner.application.ports.identity import UserRecord
from scanner.application.ports.sessions import RevokeReason, SessionRepository
from scanner.interfaces.api.deps import (
    get_access_tokens,
    get_accounts,
    get_clock,
    get_session_repository,
    get_sessions,
)
from scanner.interfaces.api.errors import auth_required, not_found
from scanner.interfaces.api.security import CurrentUser, require_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "scanner_refresh"

# The cookie is scoped to the refresh and logout paths. A cookie sent on every
# request is one more chance to log it, and nothing but these two rows reads
# it. §18.1 marks refresh as "🔓 (cookie)" — the cookie *is* the credential.
REFRESH_COOKIE_PATH = "/api/v1/auth"


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - the RFC 6750 scheme name
    expires_in: int
    user_id: str
    tenant_id: str


class SessionSummary(BaseModel):
    session_id: str
    device_label: str | None
    created_at: datetime
    last_used_at: datetime
    current: bool


def _set_refresh_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=int(REFRESH_TTL.total_seconds()),
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        # Strict, not Lax. Lax still sends the cookie on a top-level
        # navigation, which is enough for a CSRF that only needs the refresh
        # endpoint to be reached. Nothing here is triggered by following a
        # link, so Strict costs nothing.
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, *, secure: bool) -> None:
    # Same attributes as the set, or the browser keeps the original: a cookie
    # is identified by (name, domain, path), so clearing with a different path
    # silently leaves a live refresh token in place.
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def _clearing_header(*, secure: bool) -> dict[str, str]:
    """The same `Set-Cookie`, as a header for a raised error.

    Raising an `HTTPException` discards the injected `Response`, so a
    `delete_cookie` on it before the raise reads as correct and reaches
    nobody. Every refresh failure has to clear the cookie — otherwise the
    client keeps replaying a dead token, and on the reuse path each replay is
    another alarm about a family that is already revoked.

    Serialised by Starlette rather than hand-formatted, so the attributes
    cannot drift from `_clear_refresh_cookie`\'s.
    """
    scratch = Response()

    _clear_refresh_cookie(scratch, secure=secure)

    return {"set-cookie": scratch.headers["set-cookie"]}


def _tokens(
    user: UserRecord,
    session_id: str,
    *,
    access: AccessTokens,
    now: datetime,
) -> TokenResponse:
    return TokenResponse(
        access_token=access.mint(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            session_id=session_id,
            role=user.role,
            now=now,
        ),
        expires_in=int(ACCESS_TTL.total_seconds()),
        user_id=user.user_id,
        tenant_id=user.tenant_id,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    body: LoginBody,
    accounts: Annotated[AccountService, Depends(get_accounts)],
    sessions: Annotated[SessionService, Depends(get_sessions)],
    access: Annotated[AccessTokens, Depends(get_access_tokens)],
    clock: Annotated[object, Depends(get_clock)],
) -> TokenResponse:
    """§18.1 login. Bad credentials are `AUTH_REQUIRED`, deliberately."""

    now = clock.now()  # type: ignore[attr-defined]

    user = await accounts.authenticate(body.email, body.password)

    if user is None:
        # One code for wrong password, unknown address, disabled and deleted
        # accounts. §18.1: "deliberately same code, no user-enumeration".
        raise auth_required(request, "Invalid credentials.")

    issued = await sessions.open(
        user.user_id,
        now=now,
        device_label=_device_label(request),
        ip_created=_client_ip(request),
    )

    if issued is None:
        # A 128-bit id collision. Reported rather than retried: at that
        # probability it means the generator is broken, and a retry loop would
        # hide exactly that.
        raise auth_required(request, "Could not establish a session.")

    _set_refresh_cookie(response, issued.token, secure=_secure_cookies(request))

    return _tokens(user, issued.session.session_id, access=access, now=now)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    accounts: Annotated[AccountService, Depends(get_accounts)],
    sessions: Annotated[SessionService, Depends(get_sessions)],
    access: Annotated[AccessTokens, Depends(get_access_tokens)],
    clock: Annotated[object, Depends(get_clock)],
) -> TokenResponse:
    """§18.1 refresh. Family reuse ⇒ full revoke, per TAD §20."""

    now = clock.now()  # type: ignore[attr-defined]

    presented = request.cookies.get(REFRESH_COOKIE)

    if not presented:
        # No cookie to clear, and nothing to say beyond "sign in".
        raise auth_required(request, "No refresh token.")

    result = await sessions.refresh(presented, now=now)

    if not result.ok or result.issued is None:
        clearing = _clearing_header(secure=_secure_cookies(request))

        if result.outcome is RefreshOutcome.REUSE_DETECTED:
            raise auth_required(
                request,
                "Session ended. Sign in again.",
                code="TOKEN_REVOKED",
                headers=clearing,
            )

        raise auth_required(
            request,
            "Session expired. Sign in again.",
            headers=clearing,
        )

    user = await accounts.users.get(result.issued.session.user_id)

    if user is None or not user.can_authenticate:
        # The account was disabled or deleted while the family was live.
        # Checked here as well as at login, because a session that only
        # checked credentials once would outlive the account by its full
        # thirty-day refresh TTL.
        await sessions.sessions.revoke(
            result.issued.session.session_id,
            reason=RevokeReason.USER_REVOKED,
            revoked_at=now,
        )

        raise auth_required(
            request,
            "Session ended. Sign in again.",
            headers=_clearing_header(secure=_secure_cookies(request)),
        )

    _set_refresh_cookie(response, result.issued.token, secure=_secure_cookies(request))

    return _tokens(user, result.issued.session.session_id, access=access, now=now)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    sessions: Annotated[SessionService, Depends(get_sessions)],
    clock: Annotated[object, Depends(get_clock)],
) -> Response:
    """§18.1 logout: 204 whether or not there was a session to end.

    Not an error when the cookie is missing or stale. A logout that can fail
    leaves the client with a token it believes is live, and there is nothing
    for an attacker to learn from a successful sign-out.
    """
    presented = request.cookies.get(REFRESH_COOKIE)

    if presented:
        await sessions.end(presented, now=clock.now())  # type: ignore[attr-defined]

    _clear_refresh_cookie(response, secure=_secure_cookies(request))

    response.status_code = 204

    return response


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    user: Annotated[CurrentUser, Depends(require_user)],
    repository: Annotated[SessionRepository, Depends(get_session_repository)],
    clock: Annotated[object, Depends(get_clock)],
) -> list[SessionSummary]:
    """§18.1's session list (PRD FC-9.2)."""

    now = clock.now()  # type: ignore[attr-defined]

    return [
        SessionSummary(
            session_id=row.session_id,
            device_label=row.device_label,
            created_at=row.issued_at,
            last_used_at=row.rotated_at,
            # Which row is the caller's own. Without it the list is a set of
            # opaque ids and "revoke the other one" is a guess.
            current=row.session_id == user.session_id,
        )
        for row in await repository.list_live_for_user(user.user_id, now=now)
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    request: Request,
    session_id: str,
    user: Annotated[CurrentUser, Depends(require_user)],
    repository: Annotated[SessionRepository, Depends(get_session_repository)],
    clock: Annotated[object, Depends(get_clock)],
) -> Response:
    """§18.1: kill one family. `NOT_FOUND` when it is not the caller's.

    Ownership is checked before existence is admitted, and a family belonging
    to someone else returns the same 404 as one that never existed —
    otherwise this endpoint enumerates other people's session ids.
    """
    record = await repository.get(session_id)

    if record is None or record.user_id != user.user_id:
        raise not_found(request, "No such session.")

    await repository.revoke(
        session_id,
        reason=RevokeReason.USER_REVOKED,
        revoked_at=clock.now(),  # type: ignore[attr-defined]
    )

    return Response(status_code=204)


def _device_label(request: Request) -> str | None:
    """The user agent, truncated. Shown in the session list so a person can
    recognise their own devices; not trusted for anything."""

    agent = request.headers.get("user-agent")

    return agent[:200] if agent else None


def _client_ip(request: Request) -> str | None:
    """The peer address, not `X-Forwarded-For`.

    A forwarded header is client-controlled unless a trusted proxy is known to
    overwrite it, and no such trust boundary is configured here. Recording the
    peer is sometimes the proxy's address; recording a spoofable header would
    be worse — it would look precise and be a lie.
    """
    return request.client.host if request.client else None


def _secure_cookies(request: Request) -> bool:
    """`Secure` unless this is plain-HTTP localhost.

    Hardcoding `secure=True` would make the cookie silently undeliverable in
    local development, and the usual response to that is to hardcode `False`
    and forget. Deciding from the request scheme means production gets the
    flag and nobody has to remember.
    """
    return request.url.scheme == "https"
