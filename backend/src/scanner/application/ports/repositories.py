"""Persistence ports for the market context (DDD T1/T3/T8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.domain.common import Candle, Symbol
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    """A data-honesty ledger entry (DDD T8)."""

    id: str
    scope_type: str  # "symbol_tf" | "feed"
    incident_type: str  # "gap" | "validation_failure" | "aggregation_mismatch"
    started_at: datetime
    symbol: str | None = None
    timeframe: Timeframe | None = None
    candle_span: int = 0
    resolution: str | None = None  # "backfilled" | "unfillable" | None (open)
    resolved_at: datetime | None = None
    notes: str = ""


class SymbolRepository(Protocol):
    async def upsert_many(self, symbols: Sequence[Symbol]) -> int:
        """Insert new registry rows; update status of known ones. Returns affected count."""
        ...

    async def list_active(self) -> Sequence[Symbol]: ...

    async def get(self, exchange_symbol: str) -> Symbol | None: ...


class CandleRepository(Protocol):
    async def bulk_insert(self, candles: Sequence[Candle]) -> int:
        """Idempotent append: existing (symbol, tf, open_time) rows are left
        untouched (candles are immutable facts). Returns newly inserted count.
        """
        ...

    async def latest_open_time(self, symbol: str, timeframe: Timeframe) -> datetime | None: ...

    async def fetch_series(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Sequence[Candle]:
        """Ascending series with open_time in [start, end)."""
        ...

    async def count_series(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> int: ...


class IncidentRepository(Protocol):
    async def record(self, incident: IncidentRecord) -> None: ...

    async def resolve(
        self, incident_id: str, *, resolution: str, resolved_at: datetime
    ) -> None: ...

    async def list_open(self, symbol: str | None = None) -> Sequence[IncidentRecord]: ...

    async def list_for_series(self, symbol: str, timeframe: Timeframe) -> Sequence[IncidentRecord]:
        """All incidents (open and resolved) for one series — the continuity
        verifier must see resolved-unfillable gaps too."""
        ...
