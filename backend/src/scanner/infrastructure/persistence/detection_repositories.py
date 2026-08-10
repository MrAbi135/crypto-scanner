"""PostgreSQL repositories for detection events."""

from __future__ import annotations

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

            return bool(
                result.rowcount
            )  # type: ignore[attr-defined]

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                select(
                    EngineEventRow.event_key
                )
                .where(
                    EngineEventRow.event_key
                    == event_key
                )
                .limit(1)
            )

            return (
                result.scalar_one_or_none()
                is not None
            )
