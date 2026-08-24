"""PostgreSQL persistence for T19 `detection.signal_outcomes`."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.signal_outcomes import SignalOutcomeRecord
from scanner.infrastructure.persistence.signal_outcome_models import SignalOutcomeRow


class PgSignalOutcomeRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, outcome: SignalOutcomeRecord) -> bool:
        """Insert once. A second resolution cannot be stored at all.

        `signal_id` is the primary key, which is what T19's "exactly one row
        per resolved signal" means when written down -- the duplicate is
        refused rather than inserted and noticed later.
        """
        stmt = (
            pg_insert(SignalOutcomeRow)
            .values(
                signal_id=outcome.signal_id,
                outcome=outcome.outcome,
                resolved_at=outcome.resolved_at,
                elapsed_candles=outcome.elapsed_candles,
                mfe_r=outcome.mfe_r,
                mae_r=outcome.mae_r,
                excluded_from_stats=outcome.excluded_from_stats,
                resolution_evidence=outcome.resolution_evidence,
            )
            .on_conflict_do_nothing(index_elements=[SignalOutcomeRow.signal_id])
            .returning(SignalOutcomeRow.signal_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def get(self, signal_id: str) -> SignalOutcomeRecord | None:
        async with self._sessions() as session:
            row = await session.get(SignalOutcomeRow, signal_id)

        if row is None:
            return None

        return SignalOutcomeRecord(
            signal_id=row.signal_id,
            outcome=row.outcome,
            resolved_at=row.resolved_at,
            elapsed_candles=row.elapsed_candles,
            mfe_r=row.mfe_r,
            mae_r=row.mae_r,
            excluded_from_stats=row.excluded_from_stats,
            resolution_evidence=row.resolution_evidence,
        )
