"""PostgreSQL repositories for detection events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from scanner.application.ports.detection import (
    EngineEventRecord,
)
from scanner.infrastructure.persistence.detection_models import (
    EngineEventRow,
)
from scanner.shared import Timeframe


class PgEngineEventRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool:
        stmt = (
            pg_insert(EngineEventRow)
            .values(
                event_key=event.event_key,
                symbol=event.symbol,
                timeframe=event.timeframe.value,
                event_type=event.event_type,
                event_at=event.event_at,
                algo_version=event.algo_version,
                payload=event.payload,
                created_at=event.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    EngineEventRow.event_key,
                    EngineEventRow.event_at,
                ]
            )
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                select(EngineEventRow.event_key)
                .where(EngineEventRow.event_key == event_key)
                .limit(1)
            )

            return result.scalar_one_or_none() is not None

    async def list_events(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[EngineEventRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(EngineEventRow)
                .where(
                    EngineEventRow.symbol == symbol,
                    EngineEventRow.timeframe == timeframe.value,
                    EngineEventRow.event_at >= start,
                    EngineEventRow.event_at < end,
                )
                .order_by(
                    EngineEventRow.event_at.asc(),
                    EngineEventRow.event_type.asc(),
                    EngineEventRow.event_key.asc(),
                )
            )

            rows = result.scalars().all()

            return tuple(
                EngineEventRecord(
                    event_key=row.event_key,
                    symbol=row.symbol,
                    timeframe=Timeframe(row.timeframe),
                    event_type=row.event_type,
                    event_at=row.event_at,
                    algo_version=row.algo_version,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            )
