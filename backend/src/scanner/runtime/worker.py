"""Worker process composition root (S0.2 §9, Sprint S3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from starlette.applications import Starlette

from scanner.application.marketdata.contexts import parse_timeframes
from scanner.application.marketdata.daily_universe_job import (
    DailyUniverseJob,
)
from scanner.application.marketdata.fake_volume_job import (
    FakeVolumeJob,
    SuspectVolumeCounter,
)
from scanner.application.marketdata.liquidity_collector import (
    DailyLiquidityCollector,
)
from scanner.application.marketdata.liquidity_history import (
    LiquiditySnapshotBuilder,
)
from scanner.application.marketdata.symbol_sync import (
    SymbolSyncService,
)
from scanner.application.marketdata.universe_manager import (
    UniverseManager,
)
from scanner.application.ports import Clock
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
from scanner.infrastructure.persistence.detection_repositories import (
    PgEngineEventRepository,
)
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgLiquidityHistoryRepository,
    PgSymbolRepository,
    PgTradeAggregateRepository,
)
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import (
    build_health_app,
    run_asgi,
)

log = structlog.get_logger(__name__)


def _previous_utc_day(now: datetime) -> datetime:
    """The closed UTC day §6.6 should score.

    Midnight-exact is the common case and still needs the subtraction: at
    00:00:00 the day that just ended is yesterday, not today, and today has no
    candles yet.
    """
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


async def _seconds_until_next_utc_midnight() -> float:
    now = datetime.now(UTC)
    tomorrow = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) + timedelta(days=1)

    return max(
        0.0,
        (tomorrow - now).total_seconds(),
    )


async def _sync_symbols(sync: SymbolSyncService) -> None:
    """Mirror the venue registry. Never fatal.

    Binance being unreachable must not take the worker down -- the registry we
    already hold is still usable, and the next attempt is a day away at worst.
    """
    try:
        report = await sync.sync()

        log.info(
            "symbol_registry_synced",
            seen=report.seen,
            eligible=report.eligible,
            upserted=report.upserted,
        )
    except Exception:
        log.exception("symbol_registry_sync_failed")


async def _run_daily_universe_loop(
    job: DailyUniverseJob,
    symbols: PgSymbolRepository,
    sync: SymbolSyncService,
    fake_volume: FakeVolumeJob,
    clock: Clock,
) -> None:
    # Once at boot, before the first sleep. `market.symbols` had zero rows for
    # the entire life of the project because `sync-symbols` existed only as a
    # CLI command nobody had cause to type, and every loop below iterates
    # `list_active()` -- so an empty registry made the whole worker a no-op
    # that looked perfectly healthy.
    await _sync_symbols(sync)

    while True:
        await asyncio.sleep(await _seconds_until_next_utc_midnight())

        # Before evaluation, not after: a symbol listed today should be
        # evaluated today rather than a day late.
        await _sync_symbols(sync)

        # Observable, not active. §1.4 promotes on seven consecutive daily
        # evaluations, so a QUARANTINE symbol has to be measured to have any
        # chance of leaving QUARANTINE. Iterating `list_active()` meant the
        # loop woke every midnight, synced 733 eligible symbols, found none of
        # them ACTIVE, and did nothing -- for the life of the project.
        observable = await symbols.list_observable()

        # §6.6 is "recomputed daily" over a *closed* day, and the loop wakes at
        # midnight, so the day to score is the one that just ended.
        day = _previous_utc_day(clock.now())

        for symbol in observable:
            try:
                report = await job.run_symbol(symbol.exchange_symbol)

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

        # A separate pass, not folded into the one above: §6.6 scores a symbol
        # whether or not §1.4 could evaluate it, and a tiering failure must not
        # take the integrity check down with it.
        for symbol in observable:
            try:
                await fake_volume.run_symbol(symbol.exchange_symbol, day)
            except Exception:
                log.exception(
                    "fake_volume_evaluation_failed",
                    symbol=symbol.exchange_symbol,
                )


def main() -> None:
    settings = get_settings("worker")
    bootstrap(settings, "worker")

    @asynccontextmanager
    async def lifespan(
        app: Starlette,
    ) -> AsyncIterator[None]:
        engine = build_engine(settings.db_dsn)
        sessions = build_session_factory(engine)

        clock = SystemClock()

        symbol_repo = PgSymbolRepository(sessions)
        liquidity_history_repo = PgLiquidityHistoryRepository(sessions)

        async with httpx.AsyncClient(timeout=30.0) as client:
            rest_adapter = BinanceRestAdapter(
                client,
                RateBudget(settings.binance_weight_capacity),
                base_url=settings.binance_base_url,
            )

            collector = DailyLiquidityCollector(
                rest_adapter,
                rest_adapter,
                liquidity_history_repo,
                clock,
            )

            snapshot_builder = LiquiditySnapshotBuilder(liquidity_history_repo)

            symbol_sync = SymbolSyncService(
                rest_adapter,
                symbol_repo,
                clock,
            )

            universe_manager = UniverseManager(symbol_repo)

            job = DailyUniverseJob(
                collector,
                snapshot_builder,
                universe_manager,
            )

            fake_volume = FakeVolumeJob(
                symbol_repo,
                PgTradeAggregateRepository(sessions, clock),
                PgCandleRepository(sessions, clock),
                SuspectVolumeCounter(
                    PgEngineEventRepository(sessions),
                    parse_timeframes(settings.ingest_timeframes),
                ),
            )

            task = asyncio.create_task(
                _run_daily_universe_loop(
                    job,
                    symbol_repo,
                    symbol_sync,
                    fake_volume,
                    clock,
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
