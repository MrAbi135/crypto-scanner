"""Binance spot REST adapter for market and liquidity data.

Owns: rate-budget acquisition, bounded retries with backoff, Retry-After
honoring, decimal-exact parsing, and error translation to the platform
taxonomy. HTTP details never cross the adapter boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from scanner.application.ports import ExchangeSymbolInfo, MarketDataProvider
from scanner.application.ports.liquidity_provider import (
    LiquidityDataProvider,
    OrderBookLevel,
    OrderBookSnapshot,
    TopOfBook,
)
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

_WEIGHT_KLINES = 2
_WEIGHT_EXCHANGE_INFO = 20
_WEIGHT_BOOK_TICKER_SINGLE = 2

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 0.5


def _depth_weight(limit: int) -> int:
    """Return Binance request weight for one order-book depth request."""

    if limit < 1 or limit > 5000:
        raise ValueError("order book limit must be between 1 and 5000")

    if limit <= 100:
        return 5

    if limit <= 500:
        return 25

    if limit <= 1000:
        return 50

    return 250


class BinanceRestAdapter(
    MarketDataProvider,
    LiquidityDataProvider,
):
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

    async def fetch_symbols(
        self,
    ) -> Sequence[ExchangeSymbolInfo]:
        payload = await self._get(
            "/api/v3/exchangeInfo",
            {},
            weight=_WEIGHT_EXCHANGE_INFO,
        )

        symbols = payload.get("symbols")

        if not isinstance(symbols, list):
            raise ExternalError(
                "exchangeInfo: malformed payload (no symbols list)",
                code="BINANCE_MALFORMED",
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
            # Binance endTime is inclusive; platform range is [start, end).
            "endTime": utc_ms(end) - 1,
            "limit": min(limit, 1000),
        }

        rows = await self._get(
            "/api/v3/klines",
            params,
            weight=_WEIGHT_KLINES,
        )

        if not isinstance(rows, list):
            raise ExternalError(
                "klines: malformed payload (not a list)",
                code="BINANCE_MALFORMED",
            )

        try:
            return [
                self._parse_kline(
                    exchange_symbol,
                    timeframe,
                    row,
                )
                for row in rows
            ]
        except (
            ScannerError,
            ValueError,
            TypeError,
            IndexError,
        ) as exc:
            detail = exc.message if isinstance(exc, ScannerError) else str(exc)

            raise ExternalError(
                f"klines: unparseable/insane row from venue for {exchange_symbol}: {detail}",
                code="BINANCE_MALFORMED",
                retryable=True,
            ) from exc

    async def fetch_top_of_book(
        self,
        exchange_symbol: str,
    ) -> TopOfBook:
        """Return current best bid/ask for one symbol."""

        payload = await self._get(
            "/api/v3/ticker/bookTicker",
            {
                "symbol": exchange_symbol,
            },
            weight=_WEIGHT_BOOK_TICKER_SINGLE,
        )

        if not isinstance(payload, dict):
            raise ExternalError(
                "bookTicker: malformed payload",
                code="BINANCE_MALFORMED",
            )

        try:
            symbol = payload["symbol"]

            if not isinstance(symbol, str):
                raise TypeError("symbol is not a string")

            return TopOfBook(
                exchange_symbol=symbol,
                bid_price=parse_decimal(
                    payload["bidPrice"],
                    field="bid_price",
                ),
                bid_quantity=parse_decimal(
                    payload["bidQty"],
                    field="bid_quantity",
                ),
                ask_price=parse_decimal(
                    payload["askPrice"],
                    field="ask_price",
                ),
                ask_quantity=parse_decimal(
                    payload["askQty"],
                    field="ask_quantity",
                ),
            )
        except (
            KeyError,
            ScannerError,
            ValueError,
            TypeError,
        ) as exc:
            detail = exc.message if isinstance(exc, ScannerError) else str(exc)

            raise ExternalError(
                f"bookTicker: malformed payload for {exchange_symbol}: {detail}",
                code="BINANCE_MALFORMED",
                retryable=True,
            ) from exc

    async def fetch_order_book(
        self,
        exchange_symbol: str,
        *,
        limit: int = 1000,
    ) -> OrderBookSnapshot:
        """Return Binance order-book snapshot for one symbol."""

        weight = _depth_weight(limit)

        payload = await self._get(
            "/api/v3/depth",
            {
                "symbol": exchange_symbol,
                "limit": limit,
            },
            weight=weight,
        )

        if not isinstance(payload, dict):
            raise ExternalError(
                "depth: malformed payload",
                code="BINANCE_MALFORMED",
            )

        bids = payload.get("bids")
        asks = payload.get("asks")

        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ExternalError(
                f"depth: malformed levels for {exchange_symbol}",
                code="BINANCE_MALFORMED",
            )

        try:
            return OrderBookSnapshot(
                exchange_symbol=exchange_symbol,
                bids=tuple(
                    self._parse_order_book_level(
                        row,
                        side="bid",
                    )
                    for row in bids
                ),
                asks=tuple(
                    self._parse_order_book_level(
                        row,
                        side="ask",
                    )
                    for row in asks
                ),
            )
        except (
            ScannerError,
            ValueError,
            TypeError,
            IndexError,
        ) as exc:
            detail = exc.message if isinstance(exc, ScannerError) else str(exc)

            raise ExternalError(
                f"depth: malformed level for {exchange_symbol}: {detail}",
                code="BINANCE_MALFORMED",
                retryable=True,
            ) from exc

    @staticmethod
    def _parse_order_book_level(
        row: Any,
        *,
        side: str,
    ) -> OrderBookLevel:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError(f"{side} order-book level malformed: {row!r}")

        return OrderBookLevel(
            price=parse_decimal(
                row[0],
                field=f"{side}_price",
            ),
            quantity=parse_decimal(
                row[1],
                field=f"{side}_quantity",
            ),
        )

    @staticmethod
    def _parse_kline(
        symbol: str,
        tf: Timeframe,
        row: Any,
    ) -> Candle:
        """Parse one Binance kline array into a canonical Candle."""

        if not isinstance(row, (list, tuple)) or len(row) < 11:
            raise ExternalError(
                f"klines: malformed row for {symbol}: {row!r}",
                code="BINANCE_MALFORMED",
            )

        return Candle(
            symbol=symbol,
            timeframe=tf,
            open_time=utc_from_ms(int(row[0])),
            open=parse_decimal(
                row[1],
                field="open",
            ),
            high=parse_decimal(
                row[2],
                field="high",
            ),
            low=parse_decimal(
                row[3],
                field="low",
            ),
            close=parse_decimal(
                row[4],
                field="close",
            ),
            volume=parse_decimal(
                row[5],
                field="volume",
            ),
            quote_volume=parse_decimal(
                row[7],
                field="quote_volume",
            ),
            taker_buy_volume=parse_decimal(
                row[9],
                field="taker_buy_volume",
            ),
            trade_count=int(row[8]),
            source=CandleSource.BACKFILL,
        )

    async def _get(
        self,
        path: str,
        params: dict[str, Any],
        *,
        weight: int,
    ) -> Any:
        last_error: Exception | None = None

        for attempt in range(
            1,
            _MAX_ATTEMPTS + 1,
        ):
            await self._budget.acquire(weight)

            try:
                response = await self._client.get(
                    self._base_url + path,
                    params=params,
                )
            except httpx.HTTPError as exc:
                last_error = exc

                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))

                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExternalError(
                        "binance returned non-JSON body",
                        code="BINANCE_MALFORMED",
                        retryable=True,
                    ) from exc

            if response.status_code in (
                418,
                429,
            ):
                retry_after = float(
                    response.headers.get(
                        "Retry-After",
                        "5",
                    )
                )

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

            raise ExternalError(
                f"binance rejected request ({response.status_code}): {response.text[:200]}",
                code="BINANCE_REJECTED",
            )

        raise ExternalError(
            f"binance unreachable after {_MAX_ATTEMPTS} attempts: {last_error}",
            code="BINANCE_UNREACHABLE",
            retryable=True,
        ) from last_error
