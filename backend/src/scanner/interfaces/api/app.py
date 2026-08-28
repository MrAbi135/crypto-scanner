"""Read API assembly (Sprint S10a, Roadmap §7.2 step 3).

Only the endpoint rows S13a consumes. Everything else in the API Specification
stays `DESIGNED` (§15) -- the spec being frozen and complete does not oblige
implementing it in one pass, but it does forbid inventing rows that are not in
it.

## Authentication, as of S10-minimal

The tripwire this module used to carry — `allow_unauthenticated=True`, refusing
to build otherwise — is gone, because the thing it was standing in for now
exists. Every read row requires a bearer token from §18.1's login.

What is *not* here is entitlements. TAD §21 layers authentication, tenant
scoping, entitlements and RBAC; S10-minimal builds the first and leaves the
rest. A token proves who is calling; nothing yet decides what their plan
permits, because there are no plans. Every authenticated caller sees
everything, and that is correct for a single-operator instance and wrong the
moment there are two.

`ENTITLEMENTS_ENFORCED = False` says so in code rather than in a comment, so
the check that lands with plans has something to flip and a test has something
to assert against.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from scanner.application.feed import LiveFeedService
from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository
from scanner.application.ports.repositories import IncidentRepository, SymbolRepository
from scanner.application.ports.sessions import SessionRepository
from scanner.application.ports.signal_outcomes import SignalOutcomeRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRepository
from scanner.application.ports.track_record import (
    TrackRecordRepository,
    TrackRecordStatistics,
)
from scanner.application.ranking import RankingSnapshotService
from scanner.interfaces.api.auth import router as auth_router
from scanner.interfaces.api.coins import router as coins_router
from scanner.interfaces.api.dashboard import router as dashboard_router
from scanner.interfaces.api.errors import install_error_handlers
from scanner.interfaces.api.market import router as market_router
from scanner.interfaces.api.me import router as me_router
from scanner.interfaces.api.query import CursorCodec
from scanner.interfaces.api.rankings import router as rankings_router
from scanner.interfaces.api.ratelimit import (
    InMemoryRateLimitStore,
    RateLimitStore,
    assert_every_row_has_a_class,
    enforce_rate_limit,
)
from scanner.interfaces.api.scanner import router as scanner_router
from scanner.interfaces.api.security import require_user
from scanner.interfaces.api.signals import router as signals_router

# Kept in the code so a reader can see the subset at a glance, and so the
# contract test can assert that nothing was quietly added.
IMPLEMENTED_ROWS: tuple[str, ...] = (
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /api/v1/auth/sessions",
    "GET /api/v1/me",
    "GET /api/v1/rankings",
    "GET /api/v1/scanner/feed",
    "GET /api/v1/market/incidents",
    "GET /api/v1/scanner/universe",
    "GET /api/v1/dashboard/status",
    "GET /api/v1/rankings/weights",
    "GET /api/v1/signals/history",
    "GET /api/v1/signals/statistics",
    "GET /api/v1/signals/{signal_id}",
    "GET /api/v1/signals/{signal_id}/evidence",
    "GET /api/v1/signals/{signal_id}/transitions",
    "DELETE /api/v1/auth/sessions/{session_id}",
    "GET /api/v1/market/candles",
    "GET /api/v1/coins/{symbol_id}/structure",
    "GET /api/v1/coins/{symbol_id}/zones",
    "GET /api/v1/coins/{symbol_id}/liquidity",
)

# TAD §21's third layer. False until plans exist; a route that consults it
# today would be a check that cannot fail.
ENTITLEMENTS_ENFORCED = False


def build_read_api(
    *,
    candles: CandleRepository,
    evidence: IctEvidenceRepository,
    zones: IctZoneRepository,
    pools: LiquidityPoolRepository,
    clock: Clock,
    accounts: AccountService,
    sessions: SessionService,
    session_repository: SessionRepository,
    access_tokens: AccessTokens,
    signals: SignalRepository,
    signal_transitions: SignalTransitionRepository,
    outcomes: SignalOutcomeRepository,
    track_record: TrackRecordRepository,
    track_statistics: TrackRecordStatistics,
    rankings: RankingSnapshotService,
    feed: LiveFeedService,
    incidents: IncidentRepository,
    symbols: SymbolRepository,
    # §11's buckets. Defaulted, unlike every other collaborator here, because
    # the in-process store is the correct one for a single-container
    # deployment and a test that does not care about limits should not have to
    # build one. The Redis store, when there is one, is passed here.
    rate_limits: RateLimitStore | None = None,
) -> FastAPI:
    """Assemble the API. Every identity collaborator is required.

    None of these have defaults. A default would let a caller build an app
    that looks authenticated and is not, which is precisely the failure the
    removed `allow_unauthenticated` tripwire existed to prevent — and a
    default is a quieter version of it.
    """
    app = FastAPI(
        title="scanner-read-api",
        version="v1",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        # §11 on every row, including the unauthenticated auth ones -- those
        # are the rows a limiter matters most on. Declared here rather than per
        # router so a future router cannot be mounted outside it.
        dependencies=[Depends(enforce_rate_limit)],
    )

    app.state.candles = candles
    app.state.evidence = evidence
    app.state.zones = zones
    app.state.pools = pools
    app.state.clock = clock
    app.state.accounts = accounts
    app.state.sessions = sessions
    app.state.session_repository = session_repository
    app.state.access_tokens = access_tokens
    app.state.signals = signals
    app.state.signal_transitions = signal_transitions
    app.state.outcomes = outcomes
    app.state.track_record = track_record
    app.state.track_statistics = track_statistics
    app.state.rankings = rankings
    app.state.feed = feed
    app.state.incidents = incidents
    app.state.symbols = symbols
    # §11's buckets. Asserted here rather than discovered on a request: a row
    # added without a class would be served unlimited, and unlimited is the one
    # answer §11 does not offer.
    assert_every_row_has_a_class(IMPLEMENTED_ROWS)
    app.state.rate_limits = rate_limits or InMemoryRateLimitStore()
    # §8's cursors are signed with the same key as the access token and
    # domain-separated inside the codec, so no second secret is required.
    app.state.cursors = CursorCodec(access_tokens.secret)

    install_error_handlers(app)

    # The auth group is the way in, so it cannot require what it issues.
    app.include_router(auth_router)

    # Everything else is bearer-only, declared on the include rather than on
    # each route. TAD §21 wants a policy declaration per route; per-router is
    # the version that cannot be forgotten when a route is added, and
    # `test_every_non_auth_route_requires_a_token` enumerates the app to prove
    # no router escaped.
    protected = [Depends(require_user)]

    app.include_router(me_router, dependencies=protected)
    app.include_router(rankings_router, dependencies=protected)
    app.include_router(scanner_router, dependencies=protected)
    app.include_router(signals_router, dependencies=protected)
    app.include_router(market_router, dependencies=protected)
    app.include_router(coins_router, dependencies=protected)
    app.include_router(dashboard_router, dependencies=protected)

    return app
