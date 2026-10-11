"""PostgreSQL repositories for Sprint S5 liquidity detection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, cast, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.infrastructure.persistence.liquidity_detection_models import (
    LiquidityPoolRow,
    LiquidityTransitionRow,
)
from scanner.shared import Timeframe


class PgLiquidityPoolRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def upsert(
        self,
        pool: LiquidityPoolRecord,
    ) -> None:
        await self.upsert_many((pool,))

    async def upsert_many(
        self,
        pools: Sequence[LiquidityPoolRecord],
    ) -> None:
        """One INSERT ... ON CONFLICT for the batch, in one transaction.

        The liquidity pass used to rewrite every pool in its window one
        statement and one commit at a time -- ~130 round trips per pass,
        measured on 2026-10-11 as the largest single cost of a pass. This is
        the same statement with many VALUES rows. Postgres refuses to update
        one row twice in one statement, so a repeated id keeps only its last
        occurrence, which is what sequential upserts would have left.
        """
        latest = {pool.pool_id: pool for pool in pools}

        if not latest:
            return

        rows = list(latest.values())

        async with self._sessions() as session:
            for start in range(0, len(rows), _UPSERT_CHUNK):
                await session.execute(_pool_upsert(rows[start : start + _UPSERT_CHUNK]))

            await session.commit()

    async def get(
        self,
        pool_id: str,
    ) -> LiquidityPoolRecord | None:
        return (await self.get_many((pool_id,))).get(pool_id)

    async def get_many(
        self,
        pool_ids: Sequence[str],
    ) -> dict[str, LiquidityPoolRecord]:
        wanted = sorted(set(pool_ids))

        if not wanted:
            return {}

        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityPoolRow).where(LiquidityPoolRow.pool_id.in_(wanted))
            )

            return {row.pool_id: _pool_record(row) for row in result.scalars().all()}

    async def list_active(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        only_version: str | None = None,
    ) -> tuple[LiquidityPoolRecord, ...]:
        """ACTIVE pools, optionally only those one liquidity version wrote.

        A version bump re-hashes every pool id, so for one window after a
        deploy the table holds two generations of the same levels. Pinned, a
        reader sees one; `None` sees both, which is only right for a caller
        that means to retire the old generation.
        """
        conditions = [
            LiquidityPoolRow.symbol == symbol,
            LiquidityPoolRow.timeframe == timeframe.value,
            LiquidityPoolRow.state == "ACTIVE",
        ]

        if only_version is not None:
            conditions.append(pool_version_of(LiquidityPoolRow) == only_version)

        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityPoolRow)
                .where(*conditions)
                .order_by(
                    LiquidityPoolRow.strength.desc(),
                    LiquidityPoolRow.price.asc(),
                    LiquidityPoolRow.pool_id.asc(),
                )
            )

            rows = result.scalars().all()

            return tuple(_pool_record(row) for row in rows)

    async def transition(
        self,
        pool_id: str,
        *,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        if to_state not in {
            "SWEPT",
            "BROKEN",
            "EXPIRED",
        }:
            raise ValueError("pool transition target must be terminal")

        stmt = (
            update(LiquidityPoolRow)
            .where(
                LiquidityPoolRow.pool_id == pool_id,
                LiquidityPoolRow.state == "ACTIVE",
            )
            .values(
                state=to_state,
                updated_at=updated_at,
            )
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return bool(result.rowcount)  # type: ignore[attr-defined]


class PgLiquidityTransitionRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def append(
        self,
        transition: LiquidityTransitionRecord,
    ) -> bool:
        stmt = (
            pg_insert(LiquidityTransitionRow)
            .values(
                transition_id=transition.transition_id,
                pool_id=transition.pool_id,
                symbol=transition.symbol,
                timeframe=transition.timeframe.value,
                from_state=transition.from_state,
                to_state=transition.to_state,
                reason=transition.reason,
                transitioned_at=transition.transitioned_at,
                candle_index=transition.candle_index,
                evidence=transition.evidence,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    LiquidityTransitionRow.transition_id,
                ]
            )
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return bool(result.rowcount)  # type: ignore[attr-defined]


# 16 bound parameters per row against Postgres's 32,767 per statement; a pass
# writes ~130, so one chunk is the normal case and the bound is only a guard.
_UPSERT_CHUNK = 1000


def _pool_upsert(pools: Sequence[LiquidityPoolRecord]) -> Any:
    """The single-pool upsert's statement, for many rows at once.

    The conflict rule is unchanged: the mutable subset moves only while the
    stored row is ACTIVE, so a swept pool cannot be revived by a later pass
    (SLS §4.2 calls terminal states permanent).
    """
    stmt = pg_insert(LiquidityPoolRow).values(
        [
            {
                "pool_id": pool.pool_id,
                "symbol": pool.symbol,
                "timeframe": pool.timeframe.value,
                "side": pool.side,
                "liquidity_class": pool.liquidity_class,
                "source": pool.source,
                "price": pool.price,
                "band_low": pool.band_low,
                "band_high": pool.band_high,
                "strength": pool.strength,
                "state": pool.state,
                "member_count": pool.member_count,
                "created_index": pool.created_index,
                "created_at": pool.created_at,
                "updated_at": pool.updated_at,
                "evidence": pool.evidence,
            }
            for pool in pools
        ]
    )

    return stmt.on_conflict_do_update(
        index_elements=[LiquidityPoolRow.pool_id],
        set_={
            "liquidity_class": stmt.excluded.liquidity_class,
            # A cluster merging into a swing pool turns it into a cluster
            # pool (SLS 4.2 "merge into one pool with combined evidence").
            "source": stmt.excluded.source,
            "price": stmt.excluded.price,
            "band_low": stmt.excluded.band_low,
            "band_high": stmt.excluded.band_high,
            "strength": stmt.excluded.strength,
            "member_count": stmt.excluded.member_count,
            "updated_at": stmt.excluded.updated_at,
            "evidence": stmt.excluded.evidence,
        },
        where=(LiquidityPoolRow.state == "ACTIVE"),
    )


def _pool_record(
    row: LiquidityPoolRow,
) -> LiquidityPoolRecord:
    return LiquidityPoolRecord(
        pool_id=row.pool_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        side=row.side,
        liquidity_class=row.liquidity_class,
        source=row.source,
        price=row.price,
        band_low=row.band_low,
        band_high=row.band_high,
        strength=row.strength,
        state=row.state,
        member_count=row.member_count,
        created_index=row.created_index,
        created_at=row.created_at,
        updated_at=row.updated_at,
        evidence=row.evidence,
    )


def pool_version_of(row: type[LiquidityPoolRow]) -> ColumnElement[str]:
    """The liquidity algo version a pool row's own evidence claims.

    There is no algo_version column: detection rows carry their version inside
    their evidence (DDD v1.0.1), exactly as zones do -- see
    `ict_zone_repositories.list_live`.
    """
    version: ColumnElement[str] = cast(row.evidence, JSONB)["algo_version"].astext

    return version
