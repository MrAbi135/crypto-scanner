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

from scanner.application.feed import LiveFeedService
from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ranking import RankingSnapshotService
from scanner.config import get_settings
from scanner.config.processes import ApiSettings
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.ict_evidence_repository import (
    PgIctEvidenceRepository,
)
from scanner.infrastructure.persistence.ict_zone_repositories import (
    PgIctZoneRepository,
)
from scanner.infrastructure.persistence.identity_repositories import (
    PgTenantRepository,
    PgUserRepository,
)
from scanner.infrastructure.persistence.liquidity_detection_repositories import (
    PgLiquidityPoolRepository,
)
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.infrastructure.persistence.session_repository import (
    PgSessionRepository,
)
from scanner.infrastructure.persistence.setup_repository import PgSetupRepository
from scanner.infrastructure.persistence.signal_outcome_repository import (
    PgSignalOutcomeRepository,
)
from scanner.infrastructure.persistence.signal_repository import PgSignalRepository
from scanner.infrastructure.persistence.signal_transition_repository import (
    PgSignalTransitionRepository,
)
from scanner.infrastructure.persistence.track_record_repository import (
    PgTrackRecordRepository,
)
from scanner.interfaces.api.app import ENTITLEMENTS_ENFORCED, build_read_api
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
    # The production bail-out is gone: it existed because these rows had no
    # authentication, and now they do. What is still absent is TAD §21's
    # entitlement layer, which is a limit on *what a caller may see*, not on
    # whether they are known -- and with one operator and one plan there is
    # nothing to divide.

    clock = SystemClock()

    db = build_session_factory(build_engine(settings.db_dsn))

    users = PgUserRepository(db)
    session_repository = PgSessionRepository(db)

    # One object, two protocols: the archive read and the aggregate are
    # separate contracts over the same tables.
    archive = PgTrackRecordRepository(db)

    read_api = build_read_api(
        candles=PgCandleRepository(db, clock),
        evidence=PgIctEvidenceRepository(db),
        zones=PgIctZoneRepository(db),
        pools=PgLiquidityPoolRepository(db),
        clock=clock,
        accounts=AccountService(users, PgTenantRepository(db)),
        sessions=SessionService(session_repository),
        session_repository=session_repository,
        # Raises at boot if the configured secret is too short to be one.
        access_tokens=AccessTokens(settings.access_token_secret),
        signals=PgSignalRepository(db),
        signal_transitions=PgSignalTransitionRepository(db),
        outcomes=PgSignalOutcomeRepository(db),
        track_record=archive,
        track_statistics=archive,
        rankings=RankingSnapshotService(
            PgSetupRepository(db),
            PgSymbolRepository(db),
        ),
        incidents=PgIncidentRepository(db),
        feed=LiveFeedService(
            PgSignalRepository(db),
            PgSignalTransitionRepository(db),
            PgSymbolRepository(db),
            clock,
        ),
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            "read_api_mounted",
            env=settings.env,
            auth="bearer",
            entitlements=ENTITLEMENTS_ENFORCED,
        )

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
