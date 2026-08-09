"""Ingest process composition root (S0.2 §9, Sprint S2)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import structlog
from starlette.applications import Starlette

from scanner.application.marketdata import BackfillService
from scanner.application.marketdata.live_ingest import LiveIngestService
from scanner.config import get_settings
from scanner.domain.common import Candle
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
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
)
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import build_health_app, run_asgi
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)

_STREAMS = (
    "BTCUSDT@kline_5m",
    "ETHUSDT@kline_5m",
)

_FEEDS = (
    ("BTCUSDT", Timeframe.M5),
    ("ETHUSDT", Timeframe.M5),
)


async def _run_websocket(
    settings,
    live_ingest: LiveIngestService,
) -> None:
    url = build_combined_stream_url(
        settings.binance_ws_url,
        _STREAMS,
    )

    async def handle_candle(
        candle: Candle,
        event_at: datetime,
    ) -> None:
        inserted = await live_ingest.ingest(
            candle,
            event_at,
        )

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

    adapter = BinanceWebSocketAdapter(
        url=url,
        reconnect_delay_seconds=settings.binance_ws_reconnect_delay_seconds,
        candle_handler=handle_candle,
    )

    await adapter.run()


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

            async def readiness_probe() -> tuple[bool, dict[str, str]]:
                details: dict[str, str] = {}
                all_ready = True

                for symbol, timeframe in _FEEDS:
                    key = f"feed:{symbol}:{timeframe.value}"

                    if not live_ingest.has_observation(
                        symbol,
                        timeframe,
                    ):
                        details[key] = "NO_DATA"
                        all_ready = False
                        continue

                    state = live_ingest.freshness(
                        symbol,
                        timeframe,
                    )

                    details[key] = state.value

                    if not live_ingest.detection_allowed(
                        symbol,
                        timeframe,
                    ):
                        all_ready = False

                return all_ready, details

            app.state.readiness_probe = readiness_probe
            app.state.live_ingest = live_ingest

            task = asyncio.create_task(
                _run_websocket(
                    settings,
                    live_ingest,
                )
            )
            app.state.websocket_task = task

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
