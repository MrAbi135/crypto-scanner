"""The dependency that turns a bearer token into a caller.

TAD §21: *"REST dependency layer (route-level policy declaration — a route
without a policy declaration fails CI)"*. The CI check is not built here; what
is built is the one place a request becomes an identity, so that when the check
lands there is a single thing for it to look for.

**Deny by default.** `require_user` raises rather than returning None, so a
route that forgets to use the result still cannot proceed — a dependency that
returned an optional caller would let `if user:` be omitted and the route
would serve anonymous traffic while looking authenticated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from scanner.application.identity.tokens import AccessTokens
from scanner.interfaces.api.deps import get_access_tokens, get_clock
from scanner.interfaces.api.errors import auth_required

_SCHEME = "bearer"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The caller, as the access token asserts them.

    Not read back from the database. That is the point of a signed token —
    and the cost, stated in `tokens.py`: an account disabled after the token
    was minted stays valid until it expires. The refresh path re-reads the
    account, so the window is the access TTL rather than the session's.
    """

    user_id: str
    tenant_id: str
    session_id: str
    role: str


def _bearer(request: Request) -> str | None:
    """The token from `Authorization: Bearer <token>`.

    Header only. §19 says "tokens never in query strings" for websockets and
    the reason applies here too: query strings land in access logs, proxy
    logs, and `Referer` headers.
    """
    header = request.headers.get("authorization")

    if not header:
        return None

    scheme, _, token = header.partition(" ")

    if scheme.lower() != _SCHEME or not token.strip():
        return None

    return token.strip()


def require_user(
    request: Request,
    access: Annotated[AccessTokens, Depends(get_access_tokens)],
    clock: Annotated[object, Depends(get_clock)],
) -> CurrentUser:
    """Verify the bearer token, or refuse the request."""

    token = _bearer(request)

    if token is None:
        raise auth_required(request, "Authentication required.")

    claims = access.verify(token, now=clock.now())  # type: ignore[attr-defined]

    if claims is None:
        # Expired, wrong signature, wrong audience, malformed: one answer.
        # Telling a caller which would let them probe the token format.
        raise auth_required(request, "Authentication required.")

    return CurrentUser(
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        session_id=claims.session_id,
        role=claims.role,
    )
