"""Binance WebSocket adapter (Sprint S2)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

import structlog
import websockets

from scanner.domain.common import Candle, CandleSource, TradePrint
from scanner.shared import Timeframe, parse_decimal, utc_from_ms

log = structlog.get_logger(__name__)

CandleHandler = Callable[[Candle, datetime], Awaitable[None]]
TradeHandler = Callable[[str, TradePrint], Awaitable[None]]

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


def parse_agg_trade(event: dict[str, object]) -> tuple[str, TradePrint] | None:
    """Convert a Binance `aggTrade` event into a symbol and a print.

    **Size is the base quantity**, matching what `taker_buy_volume` means on a
    candle -- §6.5 and §6.6 both compare a trade size against other trade
    sizes on the same symbol, so the unit only has to be consistent, and
    picking the one the rest of the schema already uses keeps it so.

    Binance's `m` is *buyer is maker*, which is the taker having **sold**. The
    flag reads as the opposite of what it counts, and §2.2 keeps aggTrades
    precisely for the taker side, so it is inverted once, here.
    """
    if event.get("e") != "aggTrade":
        return None

    symbol = event.get("s")
    quantity = event.get("q")
    traded_at = event.get("T")
    buyer_is_maker = event.get("m")

    if not isinstance(symbol, str) or not isinstance(buyer_is_maker, bool):
        return None

    if not isinstance(quantity, str | int) or not isinstance(traded_at, int):
        return None

    size = parse_decimal(quantity, field="quantity")

    if size <= 0:
        # Binance does not print zero-quantity trades; a zero here is a
        # malformed frame, and `TradePrint` would refuse it anyway.
        return None

    return symbol, TradePrint(
        at=utc_from_ms(traded_at),
        size=size,
        taker_is_buyer=not buyer_is_maker,
    )


class BinanceWebSocketAdapter:
    """Receive Binance market-stream events with reconnect handling."""

    def __init__(
        self,
        url: str = "wss://stream.binance.com:9443",
        *,
        reconnect_delay_seconds: int = 5,
        candle_handler: CandleHandler | None = None,
        trade_handler: TradeHandler | None = None,
    ) -> None:
        self._url = url
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._candle_handler = candle_handler
        self._trade_handler = trade_handler
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
        trade = parse_agg_trade(event)

        if trade is not None:
            if self._trade_handler is not None:
                await self._trade_handler(*trade)

            # No log line per print. A busy symbol prints thousands a minute,
            # and §2.2's record is the minute bucket, not the tape.
            return

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
