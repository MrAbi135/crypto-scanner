"""PostgreSQL persistence for T17 `detection.signals`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.signals import SignalRecord
from scanner.infrastructure.persistence.signal_models import SignalRow
from scanner.shared import Timeframe


def _record(row: SignalRow) -> SignalRecord:
    return SignalRecord(
        signal_id=row.signal_id,
        setup_id=row.setup_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        direction=row.direction,
        archetype=row.archetype,
        grade=row.grade,
        final_confidence=row.final_confidence,
        entry_proximal=row.entry_proximal,
        entry_distal=row.entry_distal,
        invalidation_level=row.invalidation_level,
        target_bands=row.target_bands,
        published_at=row.published_at,
        ttl_candles=row.ttl_candles,
        algo_version=row.algo_version,
        param_set_version=row.param_set_version,
        payload=row.payload,
        payload_hash=row.payload_hash,
        dedup_key=row.dedup_key,
    )


class PgSignalRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, signal: SignalRecord) -> bool:
        """Insert once, or report that the id was already published.

        `ON CONFLICT DO NOTHING` and never an upsert: T17 is append-only
        forever (Constitution §45.5), and the one thing a crown-jewel record
        must not allow is a second write quietly replacing the first.
        """
        stmt = (
            pg_insert(SignalRow)
            .values(
                signal_id=signal.signal_id,
                setup_id=signal.setup_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe.value,
                direction=signal.direction,
                archetype=signal.archetype,
                grade=signal.grade,
                final_confidence=signal.final_confidence,
                entry_proximal=signal.entry_proximal,
                entry_distal=signal.entry_distal,
                invalidation_level=signal.invalidation_level,
                target_bands=signal.target_bands,
                published_at=signal.published_at,
                ttl_candles=signal.ttl_candles,
                algo_version=signal.algo_version,
                param_set_version=signal.param_set_version,
                payload=signal.payload,
                payload_hash=signal.payload_hash,
                dedup_key=signal.dedup_key,
            )
            .on_conflict_do_nothing(index_elements=[SignalRow.signal_id])
            .returning(SignalRow.signal_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def latest_for_dedup_key(self, dedup_key: str) -> SignalRecord | None:
        stmt = (
            select(SignalRow)
            .where(SignalRow.dedup_key == dedup_key)
            .order_by(SignalRow.published_at.desc(), SignalRow.signal_id.desc())
            .limit(1)
        )

        async with self._sessions() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()

        return _record(row) if row is not None else None

    async def get(self, signal_id: str) -> SignalRecord | None:
        async with self._sessions() as session:
            row = await session.get(SignalRow, signal_id)

        return _record(row) if row is not None else None
