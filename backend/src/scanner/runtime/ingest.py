"""Ingest process composition root (S0.2 §9, Sprint S2)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette

from scanner.config import get_settings
from scanner.infrastructure.exchanges.binance.ws.adapter import (
    BinanceWebSocketAdapter,
    build_combined_stream_url,
)
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import build_health_app, run_asgi

_STREAMS = (
    "BTCUSDT@kline_5m",
    "ETHUSDT@kline_5m",
)


async def _run_websocket(settings) -> None:
    url = build_combined_stream_url(
        settings.binance_ws_url,
        _STREAMS,
    )

    adapter = BinanceWebSocketAdapter(
        url=url,
        reconnect_delay_seconds=settings.binance_ws_reconnect_delay_seconds,
    )
    await adapter.run()


def main() -> None:
    settings = get_settings("ingest")
    bootstrap(settings, "ingest")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        task = asyncio.create_task(_run_websocket(settings))
        app.state.websocket_task = task

        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = build_health_app(settings)
    app.router.lifespan_context = lifespan

    run_asgi(app, settings.health_port)


if __name__ == "__main__":
    main()
