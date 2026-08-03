"""Binance adapter: fixture-driven parsing, decimal exactness, error translation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from scanner.domain.common import CandleSource
from scanner.infrastructure.exchanges.binance import BinanceRestAdapter, RateBudget
from scanner.shared import ExternalError, Timeframe, dec

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "binance"
START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 1, 1, 3, tzinfo=UTC)


def _adapter(handler) -> BinanceRestAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return BinanceRestAdapter(
        client, RateBudget(capacity=100000), base_url="https://api.binance.com"
    )


async def test_klines_parse_to_exact_decimals() -> None:
    payload = json.loads((FIXTURES / "klines_btcusdt_h1.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["interval"] == "1h"
        # our [start, end) becomes Binance-inclusive endTime = end-1ms
        assert int(request.url.params["endTime"]) == int(END.timestamp() * 1000) - 1
        return httpx.Response(200, json=payload)

    candles = await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=1000)
    assert len(candles) == 3
    first = candles[0]
    assert first.open == dec("42283.58")  # exact — string-parsed, float never existed
    assert first.taker_buy_volume == dec("610.87334000")
    assert first.open_time == START
    assert first.source is CandleSource.BACKFILL
    assert candles[1].open_time - candles[0].open_time == Timeframe.H1.duration


async def test_exchange_info_maps_registry() -> None:
    payload = json.loads((FIXTURES / "exchange_info.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(200, json=payload)

    infos = await _adapter(handler).fetch_symbols()
    by_symbol = {i.exchange_symbol: i for i in infos}
    assert by_symbol["BTCUSDT"].trading is True
    assert by_symbol["BTCUSDT"].quote_asset == "USDT"
    assert by_symbol["LUNAUSDT"].trading is False  # BREAK status maps to non-trading


async def test_client_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with pytest.raises(ExternalError) as excinfo:
        await _adapter(handler).fetch_candles("NOPEUSDT", Timeframe.H1, START, END, limit=10)
    assert excinfo.value.code == "BINANCE_REJECTED"
    assert calls == 1  # 4xx = our fault = no retry


async def test_server_error_retries_then_translates() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(ExternalError) as excinfo:
        await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=10)
    assert excinfo.value.code == "BINANCE_UNREACHABLE"
    assert excinfo.value.retryable is True
    assert calls == 4  # bounded attempts


async def test_recovers_after_transient_5xx() -> None:
    payload = json.loads((FIXTURES / "klines_btcusdt_h1.json").read_text())
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500) if calls == 1 else httpx.Response(200, json=payload)

    candles = await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=10)
    assert len(candles) == 3 and calls == 2


async def test_malformed_row_is_external_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[["bad-row"]])

    with pytest.raises(ExternalError) as excinfo:
        await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=10)
    assert excinfo.value.code == "BINANCE_MALFORMED"


async def test_insane_venue_row_is_external_not_domain_error() -> None:
    """A venue row violating OHLC sanity is EXTERNAL corruption — the
    adapter translates it; DomainInvariantError never leaks from the port."""
    row = [1704067200000, "100", "99", "98", "101", "10", 1704070799999, "1000", 5, "5", "500", "0"]
    # high(99) < close(101) — insane

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    with pytest.raises(ExternalError) as excinfo:
        await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=10)
    assert excinfo.value.code == "BINANCE_MALFORMED"


async def test_non_json_body_is_external_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>maintenance</html>")

    with pytest.raises(ExternalError) as excinfo:
        await _adapter(handler).fetch_candles("BTCUSDT", Timeframe.H1, START, END, limit=10)
    assert excinfo.value.code == "BINANCE_MALFORMED"


async def test_rate_budget_blocks_until_refill() -> None:
    import time

    budget = RateBudget(capacity=4, refill_per_second=1000.0)
    await budget.acquire(4)  # drain
    started = time.monotonic()
    await budget.acquire(2)  # needs refill ≈ 2ms at 1000/s
    assert time.monotonic() - started < 1.0  # returned promptly after refill, no deadlock
