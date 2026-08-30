"""PostgreSQL persistence for T16 `detection.setups`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.setups import SetupRecord
from scanner.infrastructure.persistence.setup_models import SetupRow
from scanner.shared import Timeframe


class PgSetupRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, setup: SetupRecord) -> bool:
        """Insert one candidate, or report that it was already there.

        `ON CONFLICT DO NOTHING` rather than an upsert: T16 is append-only,
        and a re-evaluated candle must not overwrite what an earlier pass
        concluded about it. The returned id is how the caller learns which of
        the two happened.
        """
        stmt = (
            pg_insert(SetupRow)
            .values(
                setup_id=setup.setup_id,
                symbol=setup.symbol,
                timeframe=setup.timeframe.value,
                direction=setup.direction,
                archetype=setup.archetype,
                gate_results=setup.gate_results,
                factor_scores=setup.factor_scores,
                adjustments=setup.adjustments,
                base_confidence=setup.base_confidence,
                final_confidence=setup.final_confidence,
                floor_passed=setup.floor_passed,
                algo_version=setup.algo_version,
                evaluated_at=setup.evaluated_at,
                evidence=setup.evidence,
            )
            .on_conflict_do_nothing(index_elements=[SetupRow.setup_id])
            .returning(SetupRow.setup_id)
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            written = result.scalar_one_or_none()

            await session.commit()

            return written is not None

    async def get(self, setup_id: str) -> SetupRecord | None:
        stmt = select(SetupRow).where(SetupRow.setup_id == setup_id)

        async with self._sessions() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()

        return _record(row) if row is not None else None

    async def list_at(
        self,
        symbols: tuple[str, ...],
        timeframe: Timeframe,
        evaluated_at: datetime,
    ) -> tuple[SetupRecord, ...]:
        if not symbols:
            return ()

        stmt = (
            select(SetupRow)
            .where(
                SetupRow.symbol.in_(symbols),
                SetupRow.timeframe == timeframe.value,
                SetupRow.evaluated_at == evaluated_at,
            )
            # Ordered so a caller that does not rank still gets a stable list.
            .order_by(SetupRow.symbol.asc(), SetupRow.direction.asc())
        )

        async with self._sessions() as session:
            rows = (await session.execute(stmt)).scalars().all()

        return tuple(_record(row) for row in rows)


def _record(row: SetupRow) -> SetupRecord:
    return SetupRecord(
        setup_id=row.setup_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        direction=row.direction,
        archetype=row.archetype,
        gate_results=row.gate_results,
        factor_scores=row.factor_scores,
        adjustments=row.adjustments,
        base_confidence=row.base_confidence,
        final_confidence=row.final_confidence,
        floor_passed=row.floor_passed,
        algo_version=row.algo_version,
        evaluated_at=row.evaluated_at,
        evidence=row.evidence,
    )
