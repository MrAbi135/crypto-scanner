"""PostgreSQL repositories for Sprint S5 liquidity detection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
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
        stmt = pg_insert(LiquidityPoolRow).values(
            pool_id=pool.pool_id,
            symbol=pool.symbol,
            timeframe=pool.timeframe.value,
            side=pool.side,
            liquidity_class=pool.liquidity_class,
            source=pool.source,
            price=pool.price,
            band_low=pool.band_low,
            band_high=pool.band_high,
            strength=pool.strength,
            state=pool.state,
            member_count=pool.member_count,
            created_index=pool.created_index,
            created_at=pool.created_at,
            updated_at=pool.updated_at,
            evidence=pool.evidence,
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[
                LiquidityPoolRow.pool_id,
            ],
            set_={
                "liquidity_class": stmt.excluded.liquidity_class,
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

        async with self._sessions() as session:
            await session.execute(stmt)
            await session.commit()

    async def get(
        self,
        pool_id: str,
    ) -> LiquidityPoolRecord | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityPoolRow).where(LiquidityPoolRow.pool_id == pool_id).limit(1)
            )

            row = result.scalar_one_or_none()

            if row is None:
                return None

            return _pool_record(row)

    async def list_active(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[LiquidityPoolRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityPoolRow)
                .where(
                    LiquidityPoolRow.symbol == symbol,
                    LiquidityPoolRow.timeframe == timeframe.value,
                    LiquidityPoolRow.state == "ACTIVE",
                )
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
