"""Worker process composition root (S0.2 §9, Sprint S3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from starlette.applications import Starlette

from scanner.application.marketdata.daily_universe_job import (
    DailyUniverseJob,
)
from scanner.application.marketdata.liquidity_collector import (
    DailyLiquidityCollector,
)
from scanner.application.marketdata.liquidity_history import (
    LiquiditySnapshotBuilder,
)
from scanner.application.marketdata.universe_manager import (
    UniverseManager,
)
from scanner.config import get_settings
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.exchanges.binance import (
    BinanceRestAdapter,
    RateBudget,
)
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.repositories import (
    PgLiquidityHistoryRepository,
    PgSymbolRepository,
)
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import (
    build_health_app,
    run_asgi,
)

log = structlog.get_logger(__name__)


async def _seconds_until_next_utc_midnight() -> float:
    now = datetime.now(UTC)
    tomorrow = (
        now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        + timedelta(days=1)
    )

    return max(
        0.0,
        (tomorrow - now).total_seconds(),
    )


async def _run_daily_universe_loop(
    job: DailyUniverseJob,
    symbols: PgSymbolRepository,
) -> None:
    while True:
        await asyncio.sleep(
            await _seconds_until_next_utc_midnight()
        )

        active_symbols = await symbols.list_active()

        for symbol in active_symbols:
            try:
                report = await job.run_symbol(
                    symbol.exchange_symbol
                )

                if report.evaluation is None:
                    log.info(
                        "daily_universe_observation_saved",
                        symbol=symbol.exchange_symbol,
                        evaluation="waiting_for_7d_history",
                    )
                    continue

                evaluation = report.evaluation

                log.info(
                    "daily_universe_evaluated",
                    symbol=symbol.exchange_symbol,
                    observed_tier=evaluation.observed_tier.value,
                    previous_tier=evaluation.previous_tier.value,
                    current_tier=evaluation.current_tier.value,
                    candidate_tier=(
                        evaluation.candidate_tier.value
                        if evaluation.candidate_tier is not None
                        else None
                    ),
                    consecutive_passes=evaluation.consecutive_passes,
                    consecutive_failures=evaluation.consecutive_failures,
                    tier_changed=evaluation.tier_changed,
                )

            except Exception:
                log.exception(
                    "daily_universe_evaluation_failed",
                    symbol=symbol.exchange_symbol,
                )


def main() -> None:
    settings = get_settings("worker")
    bootstrap(settings, "worker")

    @asynccontextmanager
    async def lifespan(
        app: Starlette,
    ) -> AsyncIterator[None]:
        engine = build_engine(
            settings.db_dsn
        )
        sessions = build_session_factory(
            engine
        )

        clock = SystemClock()

        symbol_repo = PgSymbolRepository(
            sessions
        )
        liquidity_history_repo = (
            PgLiquidityHistoryRepository(
                sessions
            )
        )

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:
            rest_adapter = BinanceRestAdapter(
                client,
                RateBudget(
                    settings.binance_weight_capacity
                ),
                base_url=settings.binance_base_url,
            )

            collector = DailyLiquidityCollector(
                rest_adapter,
                rest_adapter,
                liquidity_history_repo,
                clock,
            )

            snapshot_builder = LiquiditySnapshotBuilder(
                liquidity_history_repo
            )

            universe_manager = UniverseManager(
                symbol_repo
            )

            job = DailyUniverseJob(
                collector,
                snapshot_builder,
                universe_manager,
            )

            task = asyncio.create_task(
                _run_daily_universe_loop(
                    job,
                    symbol_repo,
                )
            )

            app.state.daily_universe_task = task

            try:
                yield
            finally:
                task.cancel()

                await asyncio.gather(
                    task,
                    return_exceptions=True,
                )

                await engine.dispose()

    app = build_health_app(settings)
    app.router.lifespan_context = lifespan

    run_asgi(
        app,
        settings.health_port,
    )


if __name__ == "__main__":
    main()
