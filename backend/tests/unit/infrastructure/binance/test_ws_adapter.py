"""Unit tests for the Sprint S2 Binance WebSocket adapter."""

from __future__ import annotations

from decimal import Decimal

from scanner.domain.common import CandleSource
from scanner.infrastructure.exchanges.binance.ws.adapter import (
    build_combined_stream_url,
    parse_agg_trade,
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


def _agg_trade(**overrides) -> dict:
    event = {
        "e": "aggTrade",
        "E": 1_787_000_000_000,
        "s": "BTCUSDT",
        "p": "60000.00",
        "q": "0.25",
        "T": 1_787_000_000_000,
        "m": False,
    }
    event.update(overrides)

    return event


def test_an_agg_trade_becomes_a_print() -> None:
    parsed = parse_agg_trade(_agg_trade())

    assert parsed is not None

    symbol, print_ = parsed

    assert symbol == "BTCUSDT"
    assert print_.size == Decimal("0.25")


def test_buyer_is_maker_means_the_taker_sold() -> None:
    """Binance's `m` reads as the opposite of what §2.2 wants counted, so it
    is inverted once, at the parse boundary."""
    _, taker_bought = parse_agg_trade(_agg_trade(m=False))  # type: ignore[misc]
    _, taker_sold = parse_agg_trade(_agg_trade(m=True))  # type: ignore[misc]

    assert taker_bought.taker_is_buyer is True
    assert taker_sold.taker_is_buyer is False


def test_another_event_type_is_not_a_trade() -> None:
    assert parse_agg_trade({"e": "kline", "s": "BTCUSDT"}) is None


def test_a_malformed_frame_is_dropped_rather_than_guessed() -> None:
    assert parse_agg_trade(_agg_trade(q="0")) is None
    assert parse_agg_trade(_agg_trade(m="false")) is None
    assert parse_agg_trade({"e": "aggTrade", "s": "BTCUSDT"}) is None
