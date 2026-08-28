"""Error helpers and the platform exception handlers (API Spec §7).

Every error leaving the API is built here, so the closed code enum and the
"never leak internals" rule live in one place rather than at each raise site.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from scanner.interfaces.api.envelope import error
from scanner.interfaces.api.query import QueryRejectedError
from scanner.shared.ids import new_ulid

log = structlog.get_logger(__name__)

CORRELATION_HEADER = "X-Correlation-Id"


def correlation_id(request: Request) -> str:
    """Reuse the client's id when it sent one, so a trace spans both sides."""
    supplied = request.headers.get(CORRELATION_HEADER)

    if supplied:
        return supplied[:64]

    return str(new_ulid())


def bad_request(
    request: Request,
    message: str,
    *,
    field: str | None = None,
) -> HTTPException:
    details = [{"field": field, "code": "INVALID", "message": message}] if field else None

    return HTTPException(
        status_code=400,
        detail=error(
            "VALIDATION_FAILED",
            message,
            correlation_id=correlation_id(request),
            details=details,
        ),
    )


def auth_required(
    request: Request,
    message: str,
    *,
    code: str = "AUTH_REQUIRED",
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """401 for every authentication failure.

    §18.1 makes the shared code explicit for login — "deliberately same code,
    no user-enumeration" — and the same reasoning covers a missing header, an
    expired token and a bad signature: a caller who can tell them apart can
    probe. `code` is overridable for exactly one case, §18.1's `TOKEN_REVOKED`
    on refresh-family reuse, where the client must know to stop retrying.
    """
    return HTTPException(
        status_code=401,
        detail=error(
            code,
            message,
            correlation_id=correlation_id(request),
        ),
        # RFC 6750: a 401 from a bearer-protected resource carries this.
        # Without it a compliant client has no way to know what to present.
        #
        # `headers` is how the refresh row clears its cookie. Raising discards
        # the injected `Response`, so anything set on it before the raise never
        # reaches the client — a `delete_cookie` there looks correct and does
        # nothing.
        headers={"WWW-Authenticate": "Bearer", **(headers or {})},
    )


def semantic_rejection(
    request: Request,
    message: str,
    *,
    field: str | None = None,
) -> HTTPException:
    """§7's 422 for a request that parsed and asked for something impossible.

    §9 and §10 both end in this: an unknown filter field, an unsupported
    operator, a sort on a rank-ordered collection. §12(4) asks for
    "field-precise details", so the offending parameter travels with it —
    without that a client sending six filters learns only that one of them was
    wrong.
    """
    details = (
        [{"field": field, "code": "SEMANTIC_REJECTION", "message": message}] if field else None
    )

    return HTTPException(
        status_code=422,
        detail=error(
            "SEMANTIC_REJECTION",
            message,
            correlation_id=correlation_id(request),
            details=details,
        ),
    )


def not_found(request: Request, message: str) -> HTTPException:
    """404 is true absence only (§7) -- never used to mask an entitlement."""
    return HTTPException(
        status_code=404,
        detail=error(
            "NOT_FOUND",
            message,
            correlation_id=correlation_id(request),
        ),
    )


def _with_budget(
    request: Request,
    headers: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """§11's budget on an error response too.

    The rate-limit dependency sets these on the injected `Response`, which
    FastAPI carries onto a served reply and nowhere else -- an endpoint that
    raises is rendered here, from a fresh response that never saw it. §11 says
    "every response carries" the budget, and a 401 is a response; a client
    refused for one reason should not have to guess whether it is also near
    the other limit.

    The exception's own headers win. `WWW-Authenticate` on a 401 is required by
    RFC 6750 and is not ours to overwrite.
    """
    budget = getattr(request.state, "rate_limit_headers", None)

    if not budget:
        return dict(headers) if headers else None

    return {**budget, **(dict(headers) if headers else {})}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # Helpers above already build the envelope. A raise from elsewhere in
        # the stack (FastAPI's own 404, say) arrives with a plain string, so it
        # is wrapped rather than passed through half-formed.
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            payload: Any = exc.detail
        else:
            payload = error(
                _code_for(exc.status_code),
                str(exc.detail),
                correlation_id=correlation_id(request),
            )

        # `exc.headers` carried through rather than dropped. RFC 6750 requires
        # `WWW-Authenticate` on a 401 from a bearer-protected resource, and a
        # handler that rebuilds the response without the headers silently
        # removes it — the body still says AUTH_REQUIRED, so nothing looks
        # wrong until a compliant client cannot work out what to present.
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=_with_budget(request, exc.headers),
        )

    @app.exception_handler(QueryRejectedError)
    async def _bad_query(request: Request, exc: QueryRejectedError) -> JSONResponse:
        """§9/§10 violations become 422s wherever they are raised.

        Registered as a handler rather than caught per endpoint: the parsers
        raise from inside a dependency, and an endpoint that forgot the
        `try` would turn a caller's typo into a 500 — which reads as our bug
        and hides theirs.
        """
        rejection = semantic_rejection(request, exc.message, field=exc.field)

        return JSONResponse(
            status_code=422,
            content=rejection.detail,
            headers=_with_budget(request, None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        cid = correlation_id(request)

        # The full exception goes to the log, correlated by the same id the
        # client receives (§7). The client gets a fixed string: no message
        # from an unexpected exception is known to be user-safe.
        log.exception(
            "api_unhandled_exception",
            correlation_id=cid,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=500,
            content=error(
                "INTERNAL",
                "An unexpected error occurred.",
                correlation_id=cid,
            ),
        )


def _code_for(status_code: int) -> str:
    return {
        400: "MALFORMED_REQUEST",
        401: "AUTH_REQUIRED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "SEMANTIC_REJECTION",
        429: "RATE_LIMITED",
        503: "DEGRADED_DEPENDENCY",
    }.get(status_code, "INTERNAL")
