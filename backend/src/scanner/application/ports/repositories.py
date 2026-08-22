"""Persistence ports for the market context (DDD T1/T3/T8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.domain.common import Candle, Symbol
from scanner.domain.common.universe import UniverseTier
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    """A data-honesty ledger entry (DDD T8)."""

    id: str
    scope_type: str
    incident_type: str
    started_at: datetime
    symbol: str | None = None
    timeframe: Timeframe | None = None
    candle_span: int = 0
    resolution: str | None = None
    resolved_at: datetime | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class UniverseStateRecord:
    """Persisted S3 tier and anti-flapping state for one symbol."""

    exchange_symbol: str
    tier: UniverseTier
    candidate_tier: UniverseTier | None = None
    consecutive_passes: int = 0
    consecutive_failures: int = 0


class SymbolRepository(Protocol):
    async def upsert_many(
        self,
        symbols: Sequence[Symbol],
    ) -> int:
        """Insert new registry rows and update lifecycle facts."""
        ...

    async def list_active(self) -> Sequence[Symbol]: ...

    async def list_observable(self) -> Sequence[Symbol]:
        """Everything the daily universe job should measure (§1.4).

        Wider than `list_active`: promotion needs seven daily observations,
        so a QUARANTINE symbol has to be measured before it can stop being
        one.
        """
        ...

    async def get(
        self,
        exchange_symbol: str,
    ) -> Symbol | None: ...

    async def get_universe_state(
        self,
        exchange_symbol: str,
    ) -> UniverseStateRecord | None:
        """Return persisted universe state for one symbol."""
        ...

    async def save_universe_state(
        self,
        state: UniverseStateRecord,
    ) -> None:
        """Persist tier and hysteresis counters."""
        ...


class CandleRepository(Protocol):
    async def bulk_insert(
        self,
        candles: Sequence[Candle],
        *,
        emit_outbox: bool = False,
    ) -> int:
        """Insert immutable candles idempotently.

        `emit_outbox` writes a `market.candle.closed` event to T39 in the same
        transaction, for exactly the candles the insert accepted. Off by
        default: backfill must not announce historical closes as live ones.
        """
        ...

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None: ...

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return ascending candles in [start, end)."""
        ...

    async def count_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int: ...


class IncidentRepository(Protocol):
    async def record(
        self,
        incident: IncidentRecord,
    ) -> None: ...

    async def resolve(
        self,
        incident_id: str,
        *,
        resolution: str,
        resolved_at: datetime,
    ) -> None: ...

    async def list_open(
        self,
        symbol: str | None = None,
    ) -> Sequence[IncidentRecord]: ...

    async def list_for_series(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> Sequence[IncidentRecord]:
        """Return all incidents for one series."""
        ...
