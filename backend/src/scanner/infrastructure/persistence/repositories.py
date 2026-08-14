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

from scanner.application.ports import (
    Clock,
    IncidentRecord,
    UniverseStateRecord,
)
from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
)
from scanner.domain.common import (
    Candle,
    CandleSource,
    Symbol,
    SymbolStatus,
)
from scanner.domain.common.universe import UniverseTier
from scanner.infrastructure.persistence.models import (
    CandleRow,
    IncidentRow,
    LiquidityHistoryRow,
    SymbolRow,
)
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
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def upsert_many(
        self,
        symbols: Sequence[Symbol],
    ) -> int:
        if not symbols:
            return 0

        rows = [
            {
                "id": symbol.id,
                "venue": symbol.venue,
                "exchange_symbol": symbol.exchange_symbol,
                "base_asset": symbol.base_asset,
                "quote_asset": symbol.quote_asset,
                "status": symbol.status.value,
                "first_seen_at": symbol.first_seen_at,
            }
            for symbol in symbols
        ]

        stmt = pg_insert(SymbolRow).values(rows)

        # Known symbols keep their id/first_seen_at/lifecycle progress; only a
        # venue-reported DELISTED transition is applied here (registry facts —
        # richer lifecycle moves belong to the S3 universe manager).
        stmt = stmt.on_conflict_do_update(
            constraint="uq_symbols_venue_exchange",
            set_={
                "status": stmt.excluded.status,
            },
            where=(stmt.excluded.status == SymbolStatus.DELISTED.value)
            & (SymbolRow.status != SymbolStatus.DELISTED.value),
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def list_active(
        self,
    ) -> Sequence[Symbol]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.status == SymbolStatus.ACTIVE.value)
                )
            ).scalars()

            return [_to_symbol(row) for row in rows]

    async def get(
        self,
        exchange_symbol: str,
    ) -> Symbol | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.exchange_symbol == exchange_symbol)
                )
            ).scalar_one_or_none()

            if row is None:
                return None

            return _to_symbol(row)

    async def get_universe_state(
        self,
        exchange_symbol: str,
    ) -> UniverseStateRecord | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.exchange_symbol == exchange_symbol)
                )
            ).scalar_one_or_none()

            if row is None:
                return None

            candidate_tier = (
                UniverseTier(row.candidate_tier) if row.candidate_tier is not None else None
            )

            return UniverseStateRecord(
                exchange_symbol=row.exchange_symbol,
                tier=UniverseTier(row.tier),
                candidate_tier=candidate_tier,
                consecutive_passes=row.consecutive_passes,
                consecutive_failures=row.consecutive_failures,
            )

    async def save_universe_state(
        self,
        state: UniverseStateRecord,
    ) -> None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(SymbolRow).where(SymbolRow.exchange_symbol == state.exchange_symbol)
                )
            ).scalar_one_or_none()

            if row is None:
                raise LookupError(f"Unknown symbol: {state.exchange_symbol}")

            row.tier = state.tier.value

            row.candidate_tier = (
                state.candidate_tier.value if state.candidate_tier is not None else None
            )

            row.consecutive_passes = state.consecutive_passes
            row.consecutive_failures = state.consecutive_failures

            await session.commit()


class PgCandleRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    async def bulk_insert(
        self,
        candles: Sequence[Candle],
    ) -> int:
        if not candles:
            return 0

        now = self._clock.now()

        records = [
            (
                candle.symbol,
                candle.timeframe.value,
                candle.open_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.quote_volume,
                candle.taker_buy_volume,
                candle.trade_count,
                candle.source.value,
                0,
                now,
            )
            for candle in candles
        ]

        async with self._sessions() as session:
            conn = await session.connection()
            raw = await conn.get_raw_connection()

            driver = raw.driver_connection

            assert driver is not None

            await conn.execute(
                text(
                    "CREATE TEMP TABLE IF NOT EXISTS "
                    "_candles_stage "
                    "(LIKE market.candles INCLUDING DEFAULTS) "
                    "ON COMMIT DROP"
                )
            )

            await driver.copy_records_to_table(
                "_candles_stage",
                records=records,
                columns=list(_CANDLE_COLUMNS),
            )

            inserted = await driver.fetchval(
                """
                WITH moved AS (
                    INSERT INTO market.candles
                    SELECT * FROM _candles_stage
                    ON CONFLICT (
                        symbol,
                        timeframe,
                        open_time
                    ) DO NOTHING
                    RETURNING 1
                )
                SELECT count(*) FROM moved
                """
            )

            await session.commit()

            return int(inserted or 0)

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None:
        async with self._sessions() as session:
            return (
                await session.execute(
                    select(CandleRow.open_time)
                    .where(
                        CandleRow.symbol == symbol,
                        CandleRow.timeframe == timeframe.value,
                    )
                    .order_by(CandleRow.open_time.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
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
                    {
                        "start": start,
                        "end": end,
                    },
                )
            ).scalars()

            return [_to_candle(row) for row in rows]

    async def count_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int:
        async with self._sessions() as session:
            value = await session.execute(
                text(
                    "SELECT count(*) "
                    "FROM market.candles "
                    "WHERE symbol = :symbol "
                    "AND timeframe = :tf "
                    "AND open_time >= :start "
                    "AND open_time < :end"
                ),
                {
                    "symbol": symbol,
                    "tf": timeframe.value,
                    "start": start,
                    "end": end,
                },
            )

            return int(value.scalar_one())


class PgIncidentRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def record(
        self,
        incident: IncidentRecord,
    ) -> None:
        async with self._sessions() as session:
            session.add(
                IncidentRow(
                    id=incident.id,
                    scope_type=incident.scope_type,
                    symbol=incident.symbol,
                    timeframe=(
                        incident.timeframe.value if incident.timeframe is not None else None
                    ),
                    incident_type=incident.incident_type,
                    started_at=incident.started_at,
                    resolved_at=incident.resolved_at,
                    candle_span=incident.candle_span,
                    resolution=incident.resolution,
                    notes=incident.notes,
                )
            )

            await session.commit()

    async def resolve(
        self,
        incident_id: str,
        *,
        resolution: str,
        resolved_at: datetime,
    ) -> None:
        async with self._sessions() as session:
            row = (
                await session.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
            ).scalar_one()

            row.resolution = resolution
            row.resolved_at = resolved_at

            await session.commit()

    async def list_open(
        self,
        symbol: str | None = None,
    ) -> Sequence[IncidentRecord]:
        stmt = select(IncidentRow).where(IncidentRow.resolved_at.is_(None))

        if symbol is not None:
            stmt = stmt.where(IncidentRow.symbol == symbol)

        async with self._sessions() as session:
            rows = (await session.execute(stmt.order_by(IncidentRow.started_at))).scalars()

            return [_to_incident(row) for row in rows]

    async def list_for_series(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> Sequence[IncidentRecord]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(IncidentRow)
                    .where(
                        IncidentRow.symbol == symbol,
                        IncidentRow.timeframe == timeframe.value,
                    )
                    .order_by(IncidentRow.started_at)
                )
            ).scalars()

            return [_to_incident(row) for row in rows]


class PgLiquidityHistoryRepository:
    """PostgreSQL persistence for daily liquidity observations."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def append(
        self,
        record: LiquidityHistoryRecord,
    ) -> None:
        stmt = (
            pg_insert(LiquidityHistoryRow)
            .values(
                exchange_symbol=record.exchange_symbol,
                observed_at=record.observed_at,
                daily_quote_volume=record.daily_quote_volume,
                spread_bps=record.spread_bps,
                depth_2pct=record.depth_2pct,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    LiquidityHistoryRow.exchange_symbol,
                    LiquidityHistoryRow.observed_at,
                ]
            )
        )

        async with self._sessions() as session:
            await session.execute(stmt)
            await session.commit()

    async def fetch_recent(
        self,
        exchange_symbol: str,
        *,
        limit: int = 7,
    ) -> Sequence[LiquidityHistoryRecord]:
        if limit <= 0:
            return []

        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(LiquidityHistoryRow)
                    .where(LiquidityHistoryRow.exchange_symbol == exchange_symbol)
                    .order_by(LiquidityHistoryRow.observed_at.desc())
                    .limit(limit)
                )
            ).scalars()

            return [_to_liquidity_history(row) for row in rows]


def _to_symbol(
    row: SymbolRow,
) -> Symbol:
    return Symbol(
        id=row.id,
        venue=row.venue,
        exchange_symbol=row.exchange_symbol,
        base_asset=row.base_asset,
        quote_asset=row.quote_asset,
        status=SymbolStatus(row.status),
        first_seen_at=row.first_seen_at,
    )


def _to_candle(
    row: CandleRow,
) -> Candle:
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


def _to_incident(
    row: IncidentRow,
) -> IncidentRecord:
    return IncidentRecord(
        id=row.id,
        scope_type=row.scope_type,
        incident_type=row.incident_type,
        started_at=row.started_at,
        symbol=row.symbol,
        timeframe=(Timeframe(row.timeframe) if row.timeframe is not None else None),
        candle_span=row.candle_span,
        resolution=row.resolution,
        resolved_at=row.resolved_at,
        notes=row.notes,
    )


def _to_liquidity_history(
    row: LiquidityHistoryRow,
) -> LiquidityHistoryRecord:
    return LiquidityHistoryRecord(
        exchange_symbol=row.exchange_symbol,
        observed_at=row.observed_at,
        daily_quote_volume=row.daily_quote_volume,
        spread_bps=row.spread_bps,
        depth_2pct=row.depth_2pct,
    )
