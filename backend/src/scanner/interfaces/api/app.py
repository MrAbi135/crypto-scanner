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

from fastapi import FastAPI

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository
from scanner.application.ports.sessions import SessionRepository
from scanner.interfaces.api.auth import router as auth_router
from scanner.interfaces.api.coins import router as coins_router
from scanner.interfaces.api.errors import install_error_handlers
from scanner.interfaces.api.market import router as market_router

# Kept in the code so a reader can see the subset at a glance, and so the
# contract test can assert that nothing was quietly added.
IMPLEMENTED_ROWS: tuple[str, ...] = (
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /api/v1/auth/sessions",
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

    install_error_handlers(app)

    app.include_router(auth_router)
    app.include_router(market_router)
    app.include_router(coins_router)

    return app
