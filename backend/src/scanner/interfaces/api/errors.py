"""Error helpers and the platform exception handlers (API Spec §7).

Every error leaving the API is built here, so the closed code enum and the
"never leak internals" rule live in one place rather than at each raise site.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from scanner.interfaces.api.envelope import error
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

        return JSONResponse(status_code=exc.status_code, content=payload)

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
