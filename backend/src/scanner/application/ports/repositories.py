"""Persistence ports for the market context (DDD T1/T3/T8)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.domain.common import Candle, Symbol, TradeAggregate
from scanner.domain.common.universe import UniverseTier
from scanner.domain.volume import WashRiskState
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
class UniverseRow:
    """A registry row with its §1.4 tiering state, for §18.4's universe read."""

    exchange_symbol: str
    base_asset: str
    quote_asset: str
    status: str
    tier: UniverseTier
    candidate_tier: UniverseTier | None
    consecutive_passes: int
    consecutive_failures: int
    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class UniverseStateRecord:
    """Persisted S3 tier and anti-flapping state for one symbol."""

    exchange_symbol: str
    tier: UniverseTier
    candidate_tier: UniverseTier | None = None
    consecutive_passes: int = 0
    consecutive_failures: int = 0


class TradeAggregateRepository(Protocol):
    """DDD T4 (SLS §2.2). Append-only; the minute bucket is the record."""

    async def append_many(self, aggregates: Sequence[TradeAggregate]) -> int:
        """Insert complete minutes, ignoring any already stored.

        Returns the number newly inserted. Idempotent because a replayed
        stream must not double-count a minute -- and because the prints it was
        folded from are gone, so a wrong row cannot be recomputed away.
        """
        ...

    async def list_between(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[TradeAggregate]: ...


class SymbolRepository(Protocol):
    async def upsert_many(
        self,
        symbols: Sequence[Symbol],
    ) -> int:
        """Insert new registry rows and update lifecycle facts."""
        ...

    async def list_active(self) -> Sequence[Symbol]: ...

    async def get_wash_risk(self, exchange_symbol: str) -> WashRiskState: ...

    async def save_wash_risk(self, exchange_symbol: str, state: WashRiskState) -> None:
        """§6.6's daily tag, with the clean-day counter it lifts on."""
        ...

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

    async def list_universe(
        self,
        *,
        status: str | None = None,
        tier: str | None = None,
        limit: int = 200,
    ) -> Sequence[UniverseRow]:
        """§18.4's universe read: the registry row and its §1.4 state together.

        One query rather than `list_observable` plus a `get_universe_state` per
        symbol. The registry holds seven hundred rows; a call per symbol would
        make the page cost grow with the exchange's listings rather than with
        the answer.
        """
        ...

    async def count_observations(self) -> Mapping[str, int]:
        """Daily liquidity observations per symbol (§1.4's first seven).

        Separate from the row because it is a count over another table, and
        because it is the number that explains the whole page: until a symbol
        has seven, no evaluation runs at all and its pass counter stays at
        zero -- which reads exactly like a dead universe layer.
        """
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

    async def newest_per_series(self) -> Sequence[tuple[str, Timeframe, datetime]]:
        """Every stored series and its newest open time, in one query.

        §18.3's status row asks about all of them at once, and a call per
        series would make the strip cost grow with the universe rather than
        with the answer. The set is derived from the candles rather than from
        configuration on purpose: the question is what has actually arrived,
        and a configured feed with no rows is exactly the thing worth seeing.
        """
        ...

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

    async def list_ledger(
        self,
        *,
        symbol: str | None = None,
        open_only: bool = False,
        limit: int = 100,
    ) -> Sequence[IncidentRecord]:
        """§18.7's ledger read: newest first, resolved entries included.

        Separate from `list_open`, which the engine uses to ask "is this series
        currently degraded" and which is right to return every open row in
        arrival order. A reader is asking a different question -- what has gone
        wrong lately -- and wants the newest first and the resolved ones too,
        because an incident that was found and fixed is the part of the ledger
        that shows the honesty working.
        """
        ...
