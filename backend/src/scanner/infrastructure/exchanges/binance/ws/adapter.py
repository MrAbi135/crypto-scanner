"""Binance WebSocket adapter (Sprint S2)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

import structlog
import websockets

from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe, parse_decimal, utc_from_ms

log = structlog.get_logger(__name__)

CandleHandler = Callable[[Candle, datetime], Awaitable[None]]

_BINANCE_INTERVALS: dict[str, Timeframe] = {
    "5m": Timeframe.M5,
    "15m": Timeframe.M15,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
    "1w": Timeframe.W1,
}


def build_combined_stream_url(base_url: str, streams: Sequence[str]) -> str:
    """Build a Binance combined-stream URL from stream names."""
    normalized = [stream.lower() for stream in streams]

    if not normalized:
        raise ValueError("at least one Binance stream is required")

    joined = "/".join(normalized)
    return f"{base_url.rstrip('/')}/stream?streams={joined}"


def unwrap_stream_event(event: dict[str, object]) -> dict[str, object]:
    """Return the payload for both raw and combined stream formats."""
    data = event.get("data")

    if isinstance(data, dict):
        return data

    return event


def parse_event_time(event: dict[str, object]) -> datetime | None:
    """Return Binance exchange event time."""
    event_time = event.get("E")

    if not isinstance(event_time, int):
        return None

    return utc_from_ms(event_time)


def parse_closed_kline(event: dict[str, object]) -> Candle | None:
    """Convert a closed Binance kline event into the canonical Candle."""
    if event.get("e") != "kline":
        return None

    kline = event.get("k")
    if not isinstance(kline, dict):
        return None

    if kline.get("x") is not True:
        return None

    interval = kline.get("i")
    if not isinstance(interval, str):
        return None

    timeframe = _BINANCE_INTERVALS.get(interval)
    if timeframe is None:
        return None

    symbol = kline.get("s")
    if not isinstance(symbol, str) or not symbol:
        return None

    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=utc_from_ms(int(kline["t"])),
        open=parse_decimal(kline["o"], field="open"),
        high=parse_decimal(kline["h"], field="high"),
        low=parse_decimal(kline["l"], field="low"),
        close=parse_decimal(kline["c"], field="close"),
        volume=parse_decimal(kline["v"], field="volume"),
        quote_volume=parse_decimal(kline["q"], field="quote_volume"),
        taker_buy_volume=parse_decimal(
            kline["V"],
            field="taker_buy_volume",
        ),
        trade_count=int(kline["n"]),
        source=CandleSource.STREAM,
    )


class BinanceWebSocketAdapter:
    """Receive Binance market-stream events with reconnect handling."""

    def __init__(
        self,
        url: str = "wss://stream.binance.com:9443",
        *,
        reconnect_delay_seconds: int = 5,
        candle_handler: CandleHandler | None = None,
    ) -> None:
        self._url = url
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._candle_handler = candle_handler
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
                        message = json.loads(raw)

                        if not isinstance(message, dict):
                            continue

                        event = unwrap_stream_event(message)
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
        candle = parse_closed_kline(event)

        if candle is None:
            return

        event_at = parse_event_time(event)

        if event_at is None:
            log.warning(
                "binance_event_timestamp_missing",
                symbol=candle.symbol,
                timeframe=candle.timeframe.value,
            )
            return

        if self._candle_handler is not None:
            await self._candle_handler(candle, event_at)

        log.info(
            "binance_candle_processed",
            symbol=candle.symbol,
            timeframe=candle.timeframe.value,
            open_time=candle.open_time.isoformat(),
            source=candle.source.value,
        )
