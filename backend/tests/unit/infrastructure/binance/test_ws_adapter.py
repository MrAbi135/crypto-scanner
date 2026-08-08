"""Unit tests for the Sprint S2 Binance WebSocket adapter."""

from __future__ import annotations

from scanner.infrastructure.exchanges.binance.ws.adapter import (
    build_combined_stream_url,
    unwrap_stream_event,
)


def test_build_combined_stream_url() -> None:
    url = build_combined_stream_url(
        "wss://stream.binance.com:9443",
        [
            "BTCUSDT@kline_1m",
            "ETHUSDT@kline_5m",
        ],
    )

    assert url == (
        "wss://stream.binance.com:9443/stream"
        "?streams=btcusdt@kline_1m/ethusdt@kline_5m"
    )


def test_build_combined_stream_url_strips_trailing_slash() -> None:
    url = build_combined_stream_url(
        "wss://stream.binance.com:9443/",
        ["BTCUSDT@kline_1m"],
    )

    assert url == (
        "wss://stream.binance.com:9443/stream"
        "?streams=btcusdt@kline_1m"
    )


def test_unwrap_combined_stream_event() -> None:
    event = {
        "stream": "btcusdt@kline_1m",
        "data": {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "i": "1m",
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
            "i": "1m",
            "x": True,
        },
    }

    assert unwrap_stream_event(event) == event
    