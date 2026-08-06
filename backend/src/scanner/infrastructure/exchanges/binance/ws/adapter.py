"""Binance WebSocket adapter (Sprint S2)."""

from __future__ import annotations

import asyncio
import json

import websockets


class BinanceWebSocketAdapter:
    """Receives Binance market stream events."""

    def __init__(
        self,
        url: str = "wss://stream.binance.com:9443/ws",
    ) -> None:
        self._url = url
        self._running = False

    async def run(self) -> None:
        """Continuously receive Binance events."""

        self._running = True

        while self._running:
            try:
                async with websockets.connect(self._url) as websocket:
                    while self._running:
                        raw = await websocket.recv()
                        event = json.loads(raw)
                        await self.handle_event(event)

            except Exception:
                await asyncio.sleep(5)

    async def handle_event(self, event: dict) -> None:
        """Process a single Binance event."""
        _ = event
