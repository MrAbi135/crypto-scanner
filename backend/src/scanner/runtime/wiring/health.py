"""Shared health/metrics server for every process (S0.2 §9, TAD §22).

Three internal routes, identical across processes:
GET /internal/health/live   -> 200 while the process is up
GET /internal/health/ready  -> 200 when hard dependencies and any
                               process-specific readiness probe are healthy
GET /internal/metrics       -> Prometheus exposition

Served by uvicorn (TDR-approved), which also owns graceful SIGTERM/SIGINT
shutdown — the process's idle loop is uvicorn's serve loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import asyncpg  # type: ignore[import-untyped]
import redis.asyncio as aioredis
import uvicorn
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from scanner.config.base import BaseProcessSettings
from scanner.infrastructure.redis.client import build_redis

# Containers must bind all interfaces; single reviewed exception (TAD §22).
_BIND_HOST = "0.0.0.0"  # noqa: S104
_READY_TIMEOUT_S = 0.5

Handler = Callable[[Request], Awaitable[Response]]
ReadinessProbe = Callable[[], Awaitable[tuple[bool, dict[str, str]]]]


async def _check_postgres(db_dsn: str) -> tuple[bool, str]:
    # asyncpg wants a bare DSN, not the SQLAlchemy "+asyncpg" dialect form.
    dsn = db_dsn.replace("+asyncpg", "")

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(dsn),
            _READY_TIMEOUT_S,
        )
        try:
            await asyncio.wait_for(
                conn.execute("SELECT 1"),
                _READY_TIMEOUT_S,
            )
        finally:
            await conn.close()
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        return False, f"unreachable: {type(exc).__name__}"

    return True, "ok"


async def _check_redis(redis_url: str) -> tuple[bool, str]:
    client: aioredis.Redis = build_redis(redis_url)

    try:
        pong = await asyncio.wait_for(
            client.ping(),
            _READY_TIMEOUT_S,
        )
    except (RedisError, OSError, TimeoutError) as exc:
        return False, f"unreachable: {type(exc).__name__}"
    finally:
        await client.aclose()

    return bool(pong), "ok" if pong else "no-pong"


async def check_readiness(
    settings: BaseProcessSettings,
) -> tuple[bool, dict[str, str]]:
    """Probe every hard dependency; ready only when all answer."""
    pg_ok, pg_detail = await _check_postgres(settings.db_dsn)
    redis_ok, redis_detail = await _check_redis(settings.redis_url)

    return (
        pg_ok and redis_ok,
        {
            "db": pg_detail,
            "redis": redis_detail,
        },
    )


async def _live(request: Request) -> Response:
    return JSONResponse({"status": "live"})


def _ready_handler(settings: BaseProcessSettings) -> Handler:
    async def ready(request: Request) -> Response:
        ok, dependencies = await check_readiness(settings)

        probe: ReadinessProbe | None = getattr(
            request.app.state,
            "readiness_probe",
            None,
        )

        if probe is not None:
            probe_ok, probe_details = await probe()
            ok = ok and probe_ok
            dependencies.update(probe_details)

        return JSONResponse(
            {
                "status": "ready" if ok else "not_ready",
                "dependencies": dependencies,
            },
            status_code=200 if ok else 503,
        )

    return ready


async def _metrics(request: Request) -> Response:
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


def mount_health(
    app: Starlette,
    settings: BaseProcessSettings,
) -> None:
    """Register the three internal routes on a Starlette or FastAPI app."""
    app.add_route(
        "/internal/health/live",
        _live,
        methods=["GET"],
    )
    app.add_route(
        "/internal/health/ready",
        _ready_handler(settings),
        methods=["GET"],
    )
    app.add_route(
        "/internal/metrics",
        _metrics,
        methods=["GET"],
    )


def build_health_app(
    settings: BaseProcessSettings,
) -> Starlette:
    """Build a Starlette app exposing the internal health routes."""
    app = Starlette()
    mount_health(app, settings)
    return app


def run_asgi(
    app: Starlette,
    port: int,
) -> None:
    """Serve an ASGI app until SIGTERM/SIGINT."""
    uvicorn.run(
        app,
        host=_BIND_HOST,
        port=port,
        log_config=None,
        access_log=False,
    )
