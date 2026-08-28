"""§11's token buckets, and the dependency that enforces them.

§11 puts this "at API layer with edge backstop (TAD §23)", so it is policy
about requests rather than doctrine about markets, and it lives here rather
than in `domain`.

**A dependency, not middleware.** `enforce_rate_limit` is declared once on the
app so no router can be mounted outside it. It has to run after the router and
after `require_user`, because it needs the matched template to know the class
and the verified caller to know the bucket -- and `BaseHTTPMiddleware` runs
before both. A first draft was middleware and had to re-implement route
matching, then keyed every authenticated caller by address because no user had
been resolved yet.

**Two things §11 states that are easy to half-do.** Every response carries
`X-RateLimit-Limit/Remaining/Reset` -- every response, not only the refused
one, because a client that can only learn its budget by exceeding it has to
exceed it. And a 429 "always includes `retry_after`", which means the refusal
has to say *when*, not merely *no*.

**The clock is injected.** The bucket takes `now` and never reads one. That is
not tidiness: the identity layer once verified token expiry against the system
clock while everything around it took an injected `now`, so tests that moved
time proved nothing about the code that mattered. A limiter is the same shape
of component and would fail the same way.

**Buckets are per process.** One API container runs today, so this is the whole
enforcement; behind two it would be twice the stated limit. `RateLimitStore` is
a protocol for exactly that reason -- the Redis implementation is a store, not
a rewrite. Nothing here detects a second instance, so that constraint lives in
this paragraph and in the deployment, which is the weakest part of the piece.
§11's own answer to the residue is the edge backstop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from fastapi import HTTPException, Request
from starlette.responses import Response

from scanner.application.ports import Clock
from scanner.interfaces.api.envelope import error
from scanner.interfaces.api.errors import correlation_id

# §11's table. `Free` only: the plan a caller is on is not on the access token
# and there is no plan in the domain, so Pro and Desk cannot be resolved and
# are therefore not offered. Charging every caller the Free rate is the honest
# reading of "we do not know your plan" -- the alternative, defaulting everyone
# to Desk, would make the limiter decorative.
#
# The classes below are the ones this API can reach. `write`, `ai`, `export`
# and `ws:connect` have no endpoints yet, and a limit on a route that does not
# exist is a number nobody has tested.
FREE_PER_MINUTE: dict[str, int] = {
    "auth": 10,
    "read:light": 120,
    "read:heavy": 30,
}

WINDOW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Decision:
    """What the caller is told, whether or not they are refused."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: datetime

    def retry_after(self, now: datetime) -> int:
        """Whole seconds until the bucket can serve one request.

        Rounded up and floored at one: a `retry_after` of zero invites an
        immediate retry that is certain to be refused again, which turns one
        rejected request into a spin.
        """
        return max(1, math.ceil((self.reset_at - now).total_seconds()))

    def headers(self, now: datetime) -> dict[str, str]:
        head = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            # Seconds-until-reset rather than an epoch. §11 does not say which,
            # and a relative number cannot be misread by a client whose clock
            # disagrees with the server's -- which, for a limiter, is the whole
            # population of clients worth worrying about.
            "X-RateLimit-Reset": str(max(0, math.ceil((self.reset_at - now).total_seconds()))),
        }

        if not self.allowed:
            head["Retry-After"] = str(self.retry_after(now))

        return head


class RateLimitStore(Protocol):
    def take(self, key: str, *, limit: int, now: datetime) -> Decision:
        """Consume one token for `key`, or refuse.

        Synchronous on purpose. An in-process store has nothing to await, and a
        protocol that is async for the sake of a store that does not exist yet
        would make every call site pretend.
        """
        ...


class InMemoryRateLimitStore:
    """A fixed window per key, held in this process.

    A fixed window rather than a sliding one, because §11 says "token-bucket
    per user ... 120/min" and a client is owed a number it can predict. The
    known cost is the boundary: a caller can spend a full bucket at 11:59:59
    and another at 12:00:00. At these limits that is a burst of twice the
    minute rate for one second, which the edge backstop exists to absorb.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, datetime]] = {}

    def take(self, key: str, *, limit: int, now: datetime) -> Decision:
        used, reset_at = self._buckets.get(key, (0, now + WINDOW))

        if now >= reset_at:
            used, reset_at = 0, now + WINDOW

        if used >= limit:
            # Not incremented. A refused request must not push the reset out,
            # or a client polling through a closed window would never reopen
            # it -- the failure mode where being rate limited is permanent.
            self._buckets[key] = (used, reset_at)

            return Decision(allowed=False, limit=limit, remaining=0, reset_at=reset_at)

        used += 1
        self._buckets[key] = (used, reset_at)

        return Decision(
            allowed=True,
            limit=limit,
            remaining=limit - used,
            reset_at=reset_at,
        )

    def forget_expired(self, now: datetime) -> int:
        """Drop windows that have closed. Returns how many went.

        Without this the map grows with every distinct caller ever seen, which
        for an IP-keyed anonymous bucket is unbounded by design.
        """
        stale = [key for key, (_, reset_at) in self._buckets.items() if now >= reset_at]

        for key in stale:
            del self._buckets[key]

        return len(stale)


# §11 assigns every row a class, and the spec's own tables carry it in a
# column. Kept as a mapping keyed on the same strings as `IMPLEMENTED_ROWS` so
# that `assert_every_row_has_a_class` can refuse a build where the two have
# drifted -- an endpoint added without deciding its class would otherwise be
# unlimited, and unlimited is the one answer §11 does not offer.
RATE_LIMIT_CLASS: dict[str, str] = {
    # §11: "Login/register/reset per IP", 10/min, with progressive lockout on
    # failures (Constitution §17.9) which is not implemented here.
    "POST /api/v1/auth/login": "auth",
    "POST /api/v1/auth/refresh": "auth",
    "POST /api/v1/auth/logout": "auth",
    "GET /api/v1/auth/sessions": "read:light",
    "DELETE /api/v1/auth/sessions/{session_id}": "read:light",
    "GET /api/v1/me": "read:light",
    # §11's `read:heavy` is "collections, history, stats". Every board and
    # every list is one; a single addressed resource is `read:light`.
    "GET /api/v1/rankings": "read:heavy",
    "GET /api/v1/rankings/weights": "read:light",
    "GET /api/v1/scanner/feed": "read:heavy",
    "GET /api/v1/market/incidents": "read:light",
    "GET /api/v1/market/candles": "read:heavy",
    "GET /api/v1/signals/history": "read:heavy",
    "GET /api/v1/signals/statistics": "read:heavy",
    "GET /api/v1/signals/{signal_id}": "read:light",
    "GET /api/v1/signals/{signal_id}/evidence": "read:light",
    "GET /api/v1/signals/{signal_id}/transitions": "read:light",
    "GET /api/v1/coins/{symbol_id}/structure": "read:heavy",
    "GET /api/v1/coins/{symbol_id}/zones": "read:heavy",
    "GET /api/v1/coins/{symbol_id}/liquidity": "read:heavy",
}


def assert_every_row_has_a_class(rows: tuple[str, ...]) -> None:
    """Every implemented row is classed, and nothing else is.

    Called from `build_read_api`, so the failure is a refused startup rather
    than a request served without a limit. The reverse direction matters as
    much: a class left behind for a row that was renamed would silently limit
    nothing while looking like coverage.
    """
    classed = set(RATE_LIMIT_CLASS)
    implemented = set(rows)

    if unclassed := implemented - classed:
        raise ValueError(f"§11: no rate-limit class for {sorted(unclassed)}")

    if orphaned := classed - implemented:
        raise ValueError(f"§11: rate-limit class for rows that do not exist: {sorted(orphaned)}")

    if unknown := {c for c in RATE_LIMIT_CLASS.values()} - set(FREE_PER_MINUTE):
        raise ValueError(f"§11: no limit defined for class {sorted(unknown)}")


def route_row(request: Request) -> str | None:
    """The `METHOD /path/{template}` this request matched, or None.

    Read from `scope["route"]`, which the router has already filled in by the
    time a dependency runs. An earlier draft did this in middleware and had to
    re-run the matching itself, because `BaseHTTPMiddleware` executes *before*
    the router -- and it also ran before `require_user`, so every authenticated
    caller was keyed by address. Both problems are the same problem: the
    enforcement was in front of the two things it needed answers from.

    The template rather than the literal path. Keyed on the path,
    `/signals/{signal_id}/evidence` would hand every signal id its own budget,
    which is no limit at all.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)

    return f"{request.method} {path}" if path else None


def caller_key(request: Request) -> str:
    """§11: "per user (authenticated) or IP (anonymous)".

    `require_user` has run by now on a protected row and left the caller on
    `request.state`. Where there is none -- an anonymous row, or a token that
    did not verify -- the client address is the subject, so an unauthenticated
    flood still costs its sender something.

    `X-Forwarded-For` is deliberately not consulted: it is caller-supplied, and
    a limiter that trusts it is escaped by setting a header. The edge is where
    a forwarded address becomes trustworthy, which is why §11 puts a backstop
    there.
    """
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "user_id", None)

    if user_id:
        return f"user:{user_id}"

    client = request.client

    return f"ip:{client.host}" if client is not None else "ip:unknown"


def enforce_rate_limit(request: Request, response: Response) -> None:
    """§11, as a dependency on every row.

    A dependency and not middleware, so that routing and authentication have
    both already happened -- see `route_row` and `caller_key` for what each
    would otherwise be guessed at.

    Headers go on `response`, which FastAPI carries onto the served reply. §11
    says "every response" carries the budget, and a client that can only learn
    its budget by exceeding it has to exceed it.
    """
    store: RateLimitStore | None = getattr(request.app.state, "rate_limits", None)
    clock: Clock | None = getattr(request.app.state, "clock", None)

    if store is None or clock is None:
        return

    row = route_row(request)
    limit_class = RATE_LIMIT_CLASS.get(row) if row else None

    if limit_class is None:
        return

    now = clock.now()
    limit = FREE_PER_MINUTE[limit_class]
    decision = store.take(f"{caller_key(request)}|{limit_class}", limit=limit, now=now)

    headers = decision.headers(now)

    response.headers.update(headers)

    # Also on `request.state`, because the headers just set on `response` are
    # only carried onto a *served* reply. A row that raises -- a 401 from
    # `require_user`, a 422 from a bad filter -- is rendered by the exception
    # handler, which builds a fresh response and never sees this one. §11 says
    # every response carries the budget, and those are responses.
    request.state.rate_limit_headers = headers

    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=error(
                "RATE_LIMITED",
                f"{limit_class} allows {limit} requests per minute",
                correlation_id=correlation_id(request),
                # §7's envelope has a place for this and §11 says a 429
                # "always includes retry_after". In the body as well as the
                # header: a client reading the JSON should not have to read
                # the headers too to know when to come back.
                retry_after=decision.retry_after(now),
            ),
            headers=decision.headers(now),
        )
