"""api process composition root (S0.2 §9, Sprint S10a).

Serves `/internal/*` plus the S10a read subset. The read API has no
authentication yet -- identity is S10-S12 -- so it is mounted only where the
deployment is not production. See `interfaces/api/app.py` for why a placeholder
auth was rejected in favour of refusing to build.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from scanner.config import get_settings
from scanner.config.processes import ApiSettings
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.repositories import PgCandleRepository
from scanner.interfaces.api.app import build_read_api
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import mount_health, run_asgi

log = structlog.get_logger(__name__)


def build_api_app(settings: ApiSettings) -> FastAPI:
    app = FastAPI(
        title="scanner-internal",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    mount_health(app, settings)

    # "prod", not "production" -- BaseProcessSettings pins the enum to
    # ^(dev|staging|prod)$, so the longer spelling would never match and the
    # unauthenticated API would mount in production. Exactly the silent
    # failure this branch exists to prevent.
    if settings.env == "prod":
        # Not a soft warning. An unauthenticated read API reachable in
        # production would expose the whole detection record to anyone who
        # found the port, and the failure would be silent -- it would simply
        # work. It stays off until S12 brings identity.
        log.info("read_api_not_mounted", reason="production requires authentication")

        return app

    clock = SystemClock()

    sessions = build_session_factory(build_engine(settings.db_dsn))

    read_api = build_read_api(
        candles=PgCandleRepository(sessions, clock),
        clock=clock,
        allow_unauthenticated=True,
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("read_api_mounted", env=settings.env, auth="NONE (S10a)")

        yield

    read_api.router.lifespan_context = _lifespan

    app.mount("", read_api)

    return app


def main() -> None:
    settings = get_settings("api")
    bootstrap(settings, "api")
    run_asgi(build_api_app(settings), settings.api_port)


if __name__ == "__main__":
    main()
