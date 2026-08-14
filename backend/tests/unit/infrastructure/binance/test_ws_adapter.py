"""Unit tests for the Sprint S2 Binance WebSocket adapter."""

from __future__ import annotations

from scanner.domain.common import CandleSource
from scanner.infrastructure.exchanges.binance.ws.adapter import (
    build_combined_stream_url,
    parse_closed_kline,
    unwrap_stream_event,
)
from scanner.shared import Timeframe


def test_build_combined_stream_url() -> None:
    url = build_combined_stream_url(
        "wss://stream.binance.com:9443",
        [
            "BTCUSDT@kline_5m",
            "ETHUSDT@kline_5m",
        ],
    )

    assert url == ("wss://stream.binance.com:9443/stream?streams=btcusdt@kline_5m/ethusdt@kline_5m")


def test_build_combined_stream_url_strips_trailing_slash() -> None:
    url = build_combined_stream_url(
        "wss://stream.binance.com:9443/",
        ["BTCUSDT@kline_5m"],
    )

    assert url == ("wss://stream.binance.com:9443/stream?streams=btcusdt@kline_5m")


def test_unwrap_combined_stream_event() -> None:
    event = {
        "stream": "btcusdt@kline_5m",
        "data": {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "i": "5m",
                "x": True,
            },
        },
    }

    assert unwrap_stream_event(event) == event["data"]


def test_unwrap_raw_stream_event() -> None:
    event = {
        "e": "kline",
        "s": "BTCUSDT",
        "k": {
            "i": "5m",
            "x": True,
        },
    }

    assert unwrap_stream_event(event) == event


def test_parse_closed_kline_returns_stream_candle() -> None:
    event = {
        "e": "kline",
        "s": "BTCUSDT",
        "k": {
            "t": 1_786_234_500_000,
            "T": 1_786_234_799_999,
            "s": "BTCUSDT",
            "i": "5m",
            "o": "100.10",
            "c": "101.20",
            "h": "102.30",
            "l": "99.90",
            "v": "12.50",
            "n": 321,
            "x": True,
            "q": "1250.75",
            "V": "7.25",
        },
    }

    candle = parse_closed_kline(event)

    assert candle is not None
    assert candle.symbol == "BTCUSDT"
    assert candle.timeframe is Timeframe.M5
    assert candle.source is CandleSource.STREAM
    assert str(candle.open) == "100.10"
    assert str(candle.close) == "101.20"
    assert str(candle.high) == "102.30"
    assert str(candle.low) == "99.90"
    assert str(candle.volume) == "12.50"
    assert str(candle.quote_volume) == "1250.75"
    assert str(candle.taker_buy_volume) == "7.25"
    assert candle.trade_count == 321


def test_parse_closed_kline_ignores_forming_candle() -> None:
    event = {
        "e": "kline",
        "k": {
            "x": False,
        },
    }

    assert parse_closed_kline(event) is None


def test_parse_closed_kline_ignores_unsupported_timeframe() -> None:
    event = {
        "e": "kline",
        "k": {
            "x": True,
            "i": "1m",
            "s": "BTCUSDT",
        },
    }

    assert parse_closed_kline(event) is None
