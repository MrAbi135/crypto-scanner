"""Binance WebSocket adapter (Sprint S2)."""

from __future__ import annotations

import asyncio
import json

import structlog
import websockets

log = structlog.get_logger(__name__)


class BinanceWebSocketAdapter:
    """Receive Binance market-stream events with reconnect handling."""

    def __init__(
        self,
        url: str = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m",
        *,
        reconnect_delay_seconds: int = 5,
    ) -> None:
        self._url = url
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._running = False

    async def run(self) -> None:
        """Continuously connect to Binance and receive stream events."""
        self._running = True

        while self._running:
            try:
                log.info("binance_ws_connecting", url=self._url)

                async with websockets.connect(self._url) as websocket:
                    log.info("binance_ws_connected", url=self._url)

                    while self._running:
                        raw = await websocket.recv()
                        event = json.loads(raw)
                        await self.handle_event(event)

            except asyncio.CancelledError:
                log.info("binance_ws_cancelled")
                raise
            except Exception as exc:
                log.warning(
                    "binance_ws_disconnected",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    reconnect_delay_seconds=self._reconnect_delay_seconds,
                )
                await asyncio.sleep(self._reconnect_delay_seconds)

    async def handle_event(self, event: dict[str, object]) -> None:
        """Process one Binance stream event."""
        event_type = event.get("e")

        if event_type == "kline":
            kline = event.get("k")

            if isinstance(kline, dict) and kline.get("x") is True:
                log.info(
                    "binance_kline_closed",
                    symbol=kline.get("s"),
                    interval=kline.get("i"),
                    open_time=kline.get("t"),
                    close_time=kline.get("T"),
                )
