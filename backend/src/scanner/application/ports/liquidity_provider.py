"""Liquidity market-data provider port (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TopOfBook:
    """Best bid/ask snapshot for one exchange symbol."""

    exchange_symbol: str
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """One price/quantity level from an order book."""

    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Order book snapshot used for ±2% liquidity depth."""

    exchange_symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]


class LiquidityDataProvider(Protocol):
    """Read-side liquidity source."""

    async def fetch_top_of_book(
        self,
        exchange_symbol: str,
    ) -> TopOfBook:
        """Return current best bid and ask."""
        ...

    async def fetch_order_book(
        self,
        exchange_symbol: str,
        *,
        limit: int = 1000,
    ) -> OrderBookSnapshot:
        """Return current order-book levels for depth calculation."""
        ...
