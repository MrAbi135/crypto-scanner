"""Ingest process composition root (S0.2 §9, Sprint S2)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import structlog
from starlette.applications import Starlette

from scanner.application.marketdata import BackfillService
from scanner.application.marketdata.contexts import (
    parse_symbols,
    parse_timeframes,
    stream_names,
)
from scanner.application.marketdata.live_ingest import LiveIngestService
from scanner.application.marketdata.outbox_relay import OutboxRelayService
from scanner.application.marketdata.trade_aggregator import TradeAggregator
from scanner.application.marketdata.warmup_backfill import WarmupBackfillService
from scanner.application.ports import CandleRepository, Clock
from scanner.config import get_settings
from scanner.config.processes import IngestSettings
from scanner.domain.common import Candle, TradePrint
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.exchanges.binance import BinanceRestAdapter, RateBudget
from scanner.infrastructure.exchanges.binance.ws.adapter import (
    BinanceWebSocketAdapter,
    build_combined_stream_url,
)
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.outbox_repository import PgOutboxRepository
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgTradeAggregateRepository,
)
from scanner.infrastructure.redis.client import build_redis
from scanner.infrastructure.redis.event_stream import RedisEventStreamPublisher
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import build_health_app, run_asgi
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)


# Fast enough that a close reaches the engine within a second or two, slow
# enough that an idle market is not a tight poll. The sweep is a single indexed
# query returning nothing when there is nothing to do.
_RELAY_INTERVAL_SECONDS = 1.0


async def _run_relay(relay: OutboxRelayService) -> None:
    """Drain the outbox onto the stream, forever.

    Lives in ingest rather than engine because ingest is what writes the outbox
    rows, and because exactly one relay may run -- see `PgOutboxRepository`.
    Tying it to the single ingest process makes that structural instead of
    something an operator has to remember.
    """
    while True:
        try:
            report = await relay.sweep()

            if report.claimed:
                log.info(
                    "outbox_relayed",
                    claimed=report.claimed,
                    published=report.published,
                    marked=report.marked,
                )
        except Exception:
            # Never fatal. Redis being down must not stop candles being
            # persisted; the events stay queued and the next sweep retries.
            log.exception("outbox_relay_sweep_failed")

        await asyncio.sleep(_RELAY_INTERVAL_SECONDS)


async def _run_websocket(
    settings: IngestSettings,
    live_ingest: LiveIngestService,
    streams: tuple[str, ...],
    trades: TradeAggregator | None,
) -> None:
    url = build_combined_stream_url(
        settings.binance_ws_url,
        streams,
    )

    async def handle_candle(
        candle: Candle,
        event_at: datetime,
    ) -> None:
        inserted = await live_ingest.ingest(
            candle,
            event_at,
        )

        # A candle close is the one moment the stream guarantees a minute
        # boundary has passed, and §2.2's buckets are otherwise held until
        # the next print arrives -- which on a quiet symbol may be a while.
        if trades is not None:
            await trades.flush_completed(candle.symbol)

        log.info(
            "live_candle_ingested",
            symbol=candle.symbol,
            timeframe=candle.timeframe.value,
            open_time=candle.open_time.isoformat(),
            inserted=inserted,
            freshness=live_ingest.freshness(
                candle.symbol,
                candle.timeframe,
            ).value,
            detection_allowed=live_ingest.detection_allowed(
                candle.symbol,
                candle.timeframe,
            ),
        )

    async def handle_trade(symbol: str, print_: TradePrint) -> None:
        if trades is not None:
            await trades.observe(symbol, print_)

    adapter = BinanceWebSocketAdapter(
        url=url,
        reconnect_delay_seconds=settings.binance_ws_reconnect_delay_seconds,
        candle_handler=handle_candle,
        trade_handler=handle_trade if trades is not None else None,
    )

    await adapter.run()


def build_readiness_probe(
    *,
    feeds: Sequence[tuple[str, Timeframe]],
    live_ingest: LiveIngestService,
    candles: CandleRepository,
    clock: Clock,
) -> Callable[[], Awaitable[tuple[bool, dict[str, str]]]]:
    """§2.12's readiness, as a function that can be tested.

    Extracted from the lifespan it used to be nested inside. A probe that
    cannot be called without standing up a process is a probe nobody tests,
    and this one was wrong for months.
    """

    async def _probe() -> tuple[bool, dict[str, str]]:
        """Two questions, kept apart.

        **Coverage** — does this feed have current data? — is a fact
        about the database, and survives a restart.

        **Freshness** (SLS §2.12) — is the pipe keeping up? — is the
        exchange-event-to-observation lag, and can only be measured
        once this process has seen a close.

        They were conflated: no observation meant not-ready, so after
        any restart every slow timeframe reported `NO_DATA` until it
        next closed. On H4 that is up to four hours, on a daily series
        a day — with perfect data on disk the whole time. Under Docker
        that is a cosmetic "unhealthy"; under the orchestrator TAD §22
        targets it is a pod that never enters service, and on a
        liveness probe a restart loop that can never end because the
        thing it waits for needs the process to stay up.

        So a feed with no observation yet is judged on coverage alone.
        `NO_DATA` now means what it says.
        """
        details: dict[str, str] = {}
        all_ready = True
        now = clock.now()

        for symbol, timeframe in feeds:
            key = f"feed:{symbol}:{timeframe.value}"

            if live_ingest.has_observation(symbol, timeframe):
                details[key] = live_ingest.freshness(symbol, timeframe).value

                if not live_ingest.detection_allowed(symbol, timeframe):
                    all_ready = False

                continue

            latest = await candles.latest_open_time(symbol, timeframe)

            if latest is None:
                details[key] = "NO_DATA"
                all_ready = False
                continue

            # `latest` is an open time, so the candle it names closed
            # one interval later. One further interval of slack is the
            # window in which the next close has not happened yet --
            # normal for every timeframe, all the time.
            closed_at = latest + timeframe.duration

            if now - closed_at <= timeframe.duration:
                # Covered, and lag not yet measured. Ready: the engine
                # reads stored candles, and this is what it will read.
                details[key] = "AWAITING_CLOSE"
                continue

            # Covered but behind. Distinguished from `NO_DATA` because
            # "nothing has ever arrived" and "arrivals stopped" call
            # for different investigations.
            details[key] = "BEHIND"
            all_ready = False

        return all_ready, details

    return _probe


def main() -> None:
    settings = get_settings("ingest")
    bootstrap(settings, "ingest")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        engine = build_engine(settings.db_dsn)
        sessions = build_session_factory(engine)
        clock = SystemClock()

        candle_repo = PgCandleRepository(
            sessions,
            clock,
        )
        incident_repo = PgIncidentRepository(sessions)

        async with httpx.AsyncClient(timeout=30.0) as client:
            rest_adapter = BinanceRestAdapter(
                client,
                RateBudget(settings.binance_weight_capacity),
                base_url=settings.binance_base_url,
            )

            backfill = BackfillService(
                rest_adapter,
                candle_repo,
                incident_repo,
                clock,
            )

            live_ingest = LiveIngestService(
                candle_repo,
                backfill,
                clock,
            )

            symbols = parse_symbols(settings.ingest_symbols)
            timeframes = parse_timeframes(settings.ingest_timeframes)

            streams = stream_names(symbols, timeframes, trades=settings.ingest_trades)
            feeds = tuple((symbol, tf) for symbol in symbols for tf in timeframes)

            # Before the socket opens. A context that reaches the gate only
            # after days of live closes is a context the engine spends those
            # days silently declining.
            await WarmupBackfillService(
                candle_repo,
                backfill,
                clock,
                target_candles=settings.warmup_backfill_candles,
            ).warm_all(symbols, timeframes)

            readiness_probe = build_readiness_probe(
                feeds=feeds,
                live_ingest=live_ingest,
                candles=candle_repo,
                clock=clock,
            )

            app.state.readiness_probe = readiness_probe
            app.state.live_ingest = live_ingest

            redis_client = build_redis(settings.redis_url)

            relay = OutboxRelayService(
                PgOutboxRepository(sessions),
                RedisEventStreamPublisher(redis_client),
                clock,
            )

            task = asyncio.create_task(
                _run_websocket(
                    settings,
                    live_ingest,
                    streams,
                    TradeAggregator(
                        PgTradeAggregateRepository(sessions, clock),
                        clock,
                    )
                    if settings.ingest_trades
                    else None,
                )
            )
            app.state.websocket_task = task

            relay_task = asyncio.create_task(_run_relay(relay))
            app.state.relay_task = relay_task

            try:
                yield
            finally:
                task.cancel()
                relay_task.cancel()
                await asyncio.gather(
                    task,
                    relay_task,
                    return_exceptions=True,
                )
                await redis_client.aclose()
                await engine.dispose()

    app = build_health_app(settings)
    app.router.lifespan_context = lifespan

    run_asgi(
        app,
        settings.health_port,
    )


if __name__ == "__main__":
    main()
