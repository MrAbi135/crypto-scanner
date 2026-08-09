"""All application ports (TAD §2.3): interfaces adapters must implement."""

from scanner.application.ports.clock import Clock
from scanner.application.ports.market_data_provider import (
    ExchangeSymbolInfo,
    MarketDataProvider,
)
from scanner.application.ports.repositories import (
    CandleRepository,
    IncidentRecord,
    IncidentRepository,
    SymbolRepository,
    UniverseStateRecord,
)

__all__ = [
    "CandleRepository",
    "Clock",
    "ExchangeSymbolInfo",
    "IncidentRecord",
    "IncidentRepository",
    "MarketDataProvider",
    "SymbolRepository",
    "UniverseStateRecord",
]
