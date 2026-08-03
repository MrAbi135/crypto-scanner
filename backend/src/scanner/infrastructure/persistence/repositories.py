"""Repository implementations (DDD T1/T3/T8; TAD §13).

Candle bulk ingestion uses the asyncpg COPY→staging→conflict-skip pattern
(DDD §21.1): COPY is the fast path, the INSERT…ON CONFLICT DO NOTHING from
staging preserves immutability (existing candle facts are never touched).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports import Clock, IncidentRecord
from scanner.domain.common import Candle, CandleSource, Symbol, SymbolStatus
from scanner.infrastructure.persistence.models import CandleRow, IncidentRow, SymbolRow
from scanner.shared import Timeframe

_CANDLE_COLUMNS = (
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "taker_buy_volume",
    "trade_count",
    "source",
    "revision",
    "inserted_at",
)


class PgSymbolRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_many(self, symbols: Sequence[Symbol]) -> int:
        if not symbols:
            return 0
        rows = [
            {
                "id": s.id,
                "venue": s.venue,
                "exchange_symbol": s.exchange_symbol,
                "base_asset": s.base_asset,
                "quote_asset": s.quote_asset,
                "status": s.status.value,
                "first_seen_at": s.first_seen_at,
            }
            for s in symbols
        ]
        stmt = pg_insert(SymbolRow).values(rows)
        # Known symbols keep their id/first_seen_at/lifecycle progress; only a
        # venue-reported DELISTED transition is applied here (registry facts —
        # richer lifecycle moves belong to the S3 universe manager).
        stmt = stmt.on_conflict_do_update(
            constraint="uq_symbols_venue_exchange",
            set_={"status": stmt.excluded.status},
            where=(stmt.excluded.status == SymbolStatus.DELISTED.value)
            & (SymbolRow.status != SymbolStatus.DELISTED.value),
        )
        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]  # CursorResult

    async def list_active(self) -> Sequence[Symbol]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.status == SymbolStatus.ACTIVE.value)
                )
            ).scalars()
            return [_to_symbol(r) for r in rows]

    async def get(self, exchange_symbol: str) -> Symbol | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.exchange_symbol == exchange_symbol)
                )
            ).scalar_one_or_none()
            return _to_symbol(row) if row is not None else None


class PgCandleRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    async def bulk_insert(self, candles: Sequence[Candle]) -> int:
        if not candles:
            return 0
        now = self._clock.now()
        records = [
            (
                c.symbol,
                c.timeframe.value,
                c.open_time,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                c.quote_volume,
                c.taker_buy_volume,
                c.trade_count,
                c.source.value,
                0,
                now,
            )
            for c in candles
        ]
        async with self._sessions() as session:
            conn = await session.connection()
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection  # asyncpg connection (TDR §26 hot path)
            assert driver is not None  # invariant: pooled asyncpg conn is live
            # Create the staging table through SQLAlchemy so the session's
            # transaction is physically begun; a raw asyncpg statement here would
            # autocommit and fire ON COMMIT DROP before the COPY (integration-proven).
            await conn.execute(
                text(
                    "CREATE TEMP TABLE IF NOT EXISTS _candles_stage "
                    "(LIKE market.candles INCLUDING DEFAULTS) ON COMMIT DROP"
                )
            )
            await driver.copy_records_to_table(
                "_candles_stage", records=records, columns=list(_CANDLE_COLUMNS)
            )
            inserted = await driver.fetchval(
                """
                WITH moved AS (
                    INSERT INTO market.candles
                    SELECT * FROM _candles_stage
                    ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
                    RETURNING 1
                )
                SELECT count(*) FROM moved
                """
            )
            await session.commit()
            return int(inserted or 0)

    async def latest_open_time(self, symbol: str, timeframe: Timeframe) -> datetime | None:
        async with self._sessions() as session:
            return (
                await session.execute(
                    select(CandleRow.open_time)
                    .where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe.value)
                    .order_by(CandleRow.open_time.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def fetch_series(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Sequence[Candle]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(CandleRow)
                    .where(
                        CandleRow.symbol == symbol,
                        CandleRow.timeframe == timeframe.value,
                        CandleRow.open_time >= bindparam("start"),
                        CandleRow.open_time < bindparam("end"),
                    )
                    .order_by(CandleRow.open_time.asc()),
                    {"start": start, "end": end},
                )
            ).scalars()
            return [_to_candle(r) for r in rows]

    async def count_series(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> int:
        async with self._sessions() as session:
            value = await session.execute(
                text(
                    "SELECT count(*) FROM market.candles "
                    "WHERE symbol = :symbol AND timeframe = :tf "
                    "AND open_time >= :start AND open_time < :end"
                ),
                {"symbol": symbol, "tf": timeframe.value, "start": start, "end": end},
            )
            return int(value.scalar_one())


class PgIncidentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, incident: IncidentRecord) -> None:
        async with self._sessions() as session:
            session.add(
                IncidentRow(
                    id=incident.id,
                    scope_type=incident.scope_type,
                    symbol=incident.symbol,
                    timeframe=incident.timeframe.value if incident.timeframe else None,
                    incident_type=incident.incident_type,
                    started_at=incident.started_at,
                    resolved_at=incident.resolved_at,
                    candle_span=incident.candle_span,
                    resolution=incident.resolution,
                    notes=incident.notes,
                )
            )
            await session.commit()

    async def resolve(self, incident_id: str, *, resolution: str, resolved_at: datetime) -> None:
        async with self._sessions() as session:
            row = (
                await session.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
            ).scalar_one()
            row.resolution = resolution
            row.resolved_at = resolved_at
            await session.commit()

    async def list_open(self, symbol: str | None = None) -> Sequence[IncidentRecord]:
        stmt = select(IncidentRow).where(IncidentRow.resolved_at.is_(None))
        if symbol is not None:
            stmt = stmt.where(IncidentRow.symbol == symbol)
        async with self._sessions() as session:
            rows = (await session.execute(stmt.order_by(IncidentRow.started_at))).scalars()
            return [_to_incident(r) for r in rows]

    async def list_for_series(self, symbol: str, timeframe: Timeframe) -> Sequence[IncidentRecord]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(IncidentRow)
                    .where(IncidentRow.symbol == symbol, IncidentRow.timeframe == timeframe.value)
                    .order_by(IncidentRow.started_at)
                )
            ).scalars()
            return [_to_incident(r) for r in rows]


def _to_symbol(row: SymbolRow) -> Symbol:
    return Symbol(
        id=row.id,
        venue=row.venue,
        exchange_symbol=row.exchange_symbol,
        base_asset=row.base_asset,
        quote_asset=row.quote_asset,
        status=SymbolStatus(row.status),
        first_seen_at=row.first_seen_at,
    )


def _to_candle(row: CandleRow) -> Candle:
    return Candle(
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        open_time=row.open_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        quote_volume=row.quote_volume,
        taker_buy_volume=row.taker_buy_volume,
        trade_count=row.trade_count,
        source=CandleSource(row.source),
    )


def _to_incident(row: IncidentRow) -> IncidentRecord:
    return IncidentRecord(
        id=row.id,
        scope_type=row.scope_type,
        incident_type=row.incident_type,
        started_at=row.started_at,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe) if row.timeframe else None,
        candle_span=row.candle_span,
        resolution=row.resolution,
        resolved_at=row.resolved_at,
        notes=row.notes,
    )
