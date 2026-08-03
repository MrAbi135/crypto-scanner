"""Binance spot REST adapter implementing the MarketDataProvider port.

Owns: rate-budget acquisition, bounded retries with backoff, Retry-After
honoring, decimal-exact parsing (prices arrive as strings and STAY exact),
and error translation to the platform taxonomy. Callers receive domain
Candles or ExternalError — never an HTTP detail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from scanner.application.ports import ExchangeSymbolInfo, MarketDataProvider
from scanner.domain.common import Candle, CandleSource
from scanner.infrastructure.exchanges.binance.rate_budget import RateBudget
from scanner.shared import (
    ExternalError,
    ScannerError,
    Timeframe,
    parse_decimal,
    utc_from_ms,
    utc_ms,
)

_INTERVALS: dict[Timeframe, str] = {
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
}

# Request weights per Binance spot API docs (2024): klines limit≤1000 → 2;
# exchangeInfo full → 20.
_WEIGHT_KLINES = 2
_WEIGHT_EXCHANGE_INFO = 20

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 0.5


class BinanceRestAdapter(MarketDataProvider):
    def __init__(
        self,
        client: httpx.AsyncClient,
        budget: RateBudget,
        *,
        base_url: str = "https://api.binance.com",
    ) -> None:
        self._client = client
        self._budget = budget
        self._base_url = base_url.rstrip("/")

    async def fetch_symbols(self) -> Sequence[ExchangeSymbolInfo]:
        payload = await self._get("/api/v3/exchangeInfo", {}, weight=_WEIGHT_EXCHANGE_INFO)
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise ExternalError(
                "exchangeInfo: malformed payload (no symbols list)", code="BINANCE_MALFORMED"
            )
        return [
            ExchangeSymbolInfo(
                exchange_symbol=item["symbol"],
                base_asset=item["baseAsset"],
                quote_asset=item["quoteAsset"],
                trading=item.get("status") == "TRADING",
            )
            for item in symbols
            if isinstance(item, dict) and "symbol" in item
        ]

    async def fetch_candles(
        self,
        exchange_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        limit: int,
    ) -> Sequence[Candle]:
        params = {
            "symbol": exchange_symbol,
            "interval": _INTERVALS[timeframe],
            "startTime": utc_ms(start),
            "endTime": utc_ms(end) - 1,  # Binance endTime is inclusive; our range is [start, end)
            "limit": min(limit, 1000),
        }
        rows = await self._get("/api/v3/klines", params, weight=_WEIGHT_KLINES)
        if not isinstance(rows, list):
            raise ExternalError("klines: malformed payload (not a list)", code="BINANCE_MALFORMED")
        try:
            return [self._parse_kline(exchange_symbol, timeframe, row) for row in rows]
        except (ScannerError, ValueError, TypeError, IndexError) as exc:
            # A venue row that cannot form a sane Candle (bad numerics, OHLC
            # violation, misaligned time) is EXTERNAL corruption — translated,
            # never propagated as a domain defect (the venue is not our domain).
            detail = exc.message if isinstance(exc, ScannerError) else str(exc)
            raise ExternalError(
                f"klines: unparseable/insane row from venue for {exchange_symbol}: {detail}",
                code="BINANCE_MALFORMED",
                retryable=True,
            ) from exc

    @staticmethod
    def _parse_kline(symbol: str, tf: Timeframe, row: Any) -> Candle:
        """Kline array per Binance docs:
        [0 openTime, 1 open, 2 high, 3 low, 4 close, 5 volume, 6 closeTime,
         7 quoteVolume, 8 trades, 9 takerBuyBase, 10 takerBuyQuote, 11 ignore]
        Prices/volumes are strings — parsed straight to Decimal, floats never exist.
        """
        if not isinstance(row, (list, tuple)) or len(row) < 11:
            raise ExternalError(
                f"klines: malformed row for {symbol}: {row!r}", code="BINANCE_MALFORMED"
            )
        return Candle(
            symbol=symbol,
            timeframe=tf,
            open_time=utc_from_ms(int(row[0])),
            open=parse_decimal(row[1], field="open"),
            high=parse_decimal(row[2], field="high"),
            low=parse_decimal(row[3], field="low"),
            close=parse_decimal(row[4], field="close"),
            volume=parse_decimal(row[5], field="volume"),
            quote_volume=parse_decimal(row[7], field="quote_volume"),
            taker_buy_volume=parse_decimal(row[9], field="taker_buy_volume"),
            trade_count=int(row[8]),
            source=CandleSource.BACKFILL,
        )

    async def _get(self, path: str, params: dict[str, Any], *, weight: int) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await self._budget.acquire(weight)
            try:
                response = await self._client.get(self._base_url + path, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExternalError(
                        "binance returned non-JSON body", code="BINANCE_MALFORMED", retryable=True
                    ) from exc

            if response.status_code in (418, 429):
                retry_after = float(response.headers.get("Retry-After", "5"))
                self._budget.penalize(retry_after)
                last_error = ExternalError(
                    f"binance rate limited ({response.status_code}), retry_after={retry_after}s",
                    code="BINANCE_RATE_LIMITED",
                    retryable=True,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                last_error = ExternalError(
                    f"binance server error {response.status_code}",
                    code="BINANCE_SERVER_ERROR",
                    retryable=True,
                )
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue

            # 4xx other than rate limits: our request is wrong — not retryable.
            raise ExternalError(
                f"binance rejected request ({response.status_code}): {response.text[:200]}",
                code="BINANCE_REJECTED",
            )

        raise ExternalError(
            f"binance unreachable after {_MAX_ATTEMPTS} attempts: {last_error}",
            code="BINANCE_UNREACHABLE",
            retryable=True,
        ) from last_error
