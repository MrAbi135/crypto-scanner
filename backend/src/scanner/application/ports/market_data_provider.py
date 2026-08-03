"""Market data provider port (TDR §29: premium providers slot in here later)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.domain.common import Candle
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class ExchangeSymbolInfo:
    """Raw registry fact from the venue, pre-domain."""

    exchange_symbol: str
    base_asset: str
    quote_asset: str
    trading: bool


class MarketDataProvider(Protocol):
    """Read-side market data source. Implementations own rate budgets,
    retries and error translation; callers see domain objects or ExternalError.
    """

    async def fetch_symbols(self) -> Sequence[ExchangeSymbolInfo]:
        """Full spot symbol registry for the venue."""
        ...

    async def fetch_candles(
        self,
        exchange_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        limit: int,
    ) -> Sequence[Candle]:
        """Closed candles with open_time in [start, end), ascending, ≤ limit."""
        ...
