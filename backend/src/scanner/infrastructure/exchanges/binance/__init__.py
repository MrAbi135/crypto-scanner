"""Binance spot adapter (S1: REST only; streams are S2)."""

from scanner.infrastructure.exchanges.binance.rate_budget import RateBudget
from scanner.infrastructure.exchanges.binance.rest import BinanceRestAdapter

__all__ = ["BinanceRestAdapter", "RateBudget"]
