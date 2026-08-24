"""PostgreSQL persistence for T10's parameter-set registry."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.param_sets import ParamSetRecord
from scanner.infrastructure.persistence.detection_models import AlgoVersionRow


def _row_id(engine: str, algo_version: str, param_set_version: str) -> str:
    """A deterministic id for the triple the unique constraint already names.

    T10's `id` is a surrogate; making it a hash of the natural key means a
    repeated registration collides on the primary key as well as the unique
    index, so `ON CONFLICT DO NOTHING` needs only one of them to hold.
    """
    raw = "|".join((engine, algo_version, param_set_version))

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


class PgParamSetRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(
        self,
        engine: str,
        algo_version: str,
        param_set_version: str,
    ) -> ParamSetRecord | None:
        stmt = select(AlgoVersionRow).where(
            AlgoVersionRow.engine == engine,
            AlgoVersionRow.version == algo_version,
            AlgoVersionRow.param_set_version == param_set_version,
        )

        async with self._sessions() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()

        # A row with no checksum predates verification -- migration 013
        # backfilled the existing rows rather than inventing digests for them.
        # It is the absence of a record, not a record of absence, so boot
        # fills it in instead of comparing against it.
        if row is None or row.checksum is None or row.param_payload is None:
            return None

        return ParamSetRecord(
            engine=row.engine,
            algo_version=row.version,
            param_set_version=row.param_set_version,
            param_payload=row.param_payload,
            checksum=row.checksum,
            sls_reference=row.sls_reference,
            deployed_at=row.deployed_at or row.created_at,
        )

    async def register(self, record: ParamSetRecord) -> None:
        stmt = (
            pg_insert(AlgoVersionRow)
            .values(
                id=_row_id(
                    record.engine,
                    record.algo_version,
                    record.param_set_version,
                ),
                engine=record.engine,
                version=record.algo_version,
                param_set_version=record.param_set_version,
                param_payload=record.param_payload,
                checksum=record.checksum,
                sls_reference=record.sls_reference,
                created_at=record.deployed_at,
                deployed_at=record.deployed_at,
            )
            # Two engine processes booting together both see no row and both
            # register. They are registering the same triple with the same
            # digest, so the loser of the race has nothing to correct.
            .on_conflict_do_nothing(
                index_elements=[
                    AlgoVersionRow.engine,
                    AlgoVersionRow.version,
                    AlgoVersionRow.param_set_version,
                ]
            )
        )

        async with self._sessions() as session:
            await session.execute(stmt)
            await session.commit()
