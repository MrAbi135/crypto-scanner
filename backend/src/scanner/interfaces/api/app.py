"""Read API assembly (Sprint S10a, Roadmap §7.2 step 3).

Only the endpoint rows S13a consumes. Everything else in the API Specification
stays `DESIGNED` (§15) -- the spec being frozen and complete does not oblige
implementing it in one pass, but it does forbid inventing rows that are not in
it.

## The unauthenticated deviation, stated plainly

Every row implemented here is marked 🔑 in the spec, several with `tf:{tf}`
entitlements. Identity is S10-S12 and does not exist yet.

Two options were available: invent a placeholder auth now and unpick it later,
or ship without and make exposure impossible by accident. The second is chosen,
because a placeholder that "works" is exactly the kind of thing that survives
to production.

So `build_read_api` refuses to construct unless the caller passes
`allow_unauthenticated=True`, which the api process only does after checking
that the deployment is not production. The guard is a tripwire, not security:
the real control is that this API is not routed publicly until S12.
"""

from __future__ import annotations

from fastapi import FastAPI

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository
from scanner.interfaces.api.coins import router as coins_router
from scanner.interfaces.api.errors import install_error_handlers
from scanner.interfaces.api.market import router as market_router

# Kept in the code so a reader can see the subset at a glance, and so the
# contract test can assert that nothing was quietly added.
IMPLEMENTED_ROWS: tuple[str, ...] = (
    "GET /api/v1/market/candles",
    "GET /api/v1/coins/{symbol_id}/structure",
    "GET /api/v1/coins/{symbol_id}/zones",
    "GET /api/v1/coins/{symbol_id}/liquidity",
)


def build_read_api(
    *,
    candles: CandleRepository,
    evidence: IctEvidenceRepository,
    zones: IctZoneRepository,
    pools: LiquidityPoolRepository,
    clock: Clock,
    allow_unauthenticated: bool,
) -> FastAPI:
    if not allow_unauthenticated:
        raise RuntimeError(
            "the S10a read API has no authentication (API Spec marks these rows "
            "as requiring it; identity lands in S10-S12). Refusing to build. "
            "Pass allow_unauthenticated=True only for a deployment that is not "
            "publicly routed."
        )

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

    install_error_handlers(app)

    app.include_router(market_router)
    app.include_router(coins_router)

    return app
