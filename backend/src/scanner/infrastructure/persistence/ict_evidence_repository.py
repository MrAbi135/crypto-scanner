"""PostgreSQL evidence reader for the Sprint S6 ICT engine."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
    RecentSweepRecord,
    ShiftEvidenceRecord,
    StructureEvidenceRecord,
)
from scanner.infrastructure.persistence.detection_models import (
    EngineEventRow,
)
from scanner.infrastructure.persistence.liquidity_detection_models import (
    LiquidityPoolRow,
    LiquidityTransitionRow,
)
from scanner.shared import Timeframe


class PgIctEvidenceRepository(IctEvidenceRepository):
    """Read deterministic S4/S5 evidence without leaking persistence upward."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def list_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[StructureEvidenceRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(EngineEventRow)
                .where(
                    EngineEventRow.symbol == symbol,
                    EngineEventRow.timeframe == timeframe.value,
                    EngineEventRow.event_at >= start,
                    EngineEventRow.event_at < end,
                    EngineEventRow.event_type.like("SWING_%")
                    | EngineEventRow.event_type.like("STRUCTURE_%"),
                )
                .order_by(
                    EngineEventRow.event_at.asc(),
                    EngineEventRow.event_type.asc(),
                    EngineEventRow.event_key.asc(),
                )
            )

            rows = result.scalars().all()

            return tuple(
                StructureEvidenceRecord(
                    event_type=row.event_type,
                    event_at=row.event_at,
                    algo_version=row.algo_version,
                    payload=row.payload,
                )
                for row in rows
            )

    async def list_shifts(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[ShiftEvidenceRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(EngineEventRow)
                .where(
                    EngineEventRow.symbol == symbol,
                    EngineEventRow.timeframe == timeframe.value,
                    EngineEventRow.event_at >= start,
                    EngineEventRow.event_at < end,
                    EngineEventRow.event_type.like("MSS_%")
                    | EngineEventRow.event_type.like("CHOCH_%"),
                )
                .order_by(
                    EngineEventRow.event_at.asc(),
                    EngineEventRow.event_key.asc(),
                )
            )

            records: list[ShiftEvidenceRecord] = []

            for row in result.scalars().all():
                payload = json.loads(row.payload)

                choch_index = payload.get("choch_index", payload.get("break_index"))
                followthrough = payload.get("followthrough_index", choch_index)

                if choch_index is None:
                    continue

                records.append(
                    ShiftEvidenceRecord(
                        event_type=row.event_type,
                        direction=str(payload.get("direction", "")),
                        choch_index=int(choch_index),
                        followthrough_index=int(followthrough),
                        event_at=row.event_at,
                        payload=row.payload,
                    )
                )

            return tuple(records)

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[LiquidityEvidenceRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityTransitionRow)
                .where(
                    LiquidityTransitionRow.symbol == symbol,
                    LiquidityTransitionRow.timeframe == timeframe.value,
                    LiquidityTransitionRow.transitioned_at >= start,
                    LiquidityTransitionRow.transitioned_at < end,
                )
                .order_by(
                    LiquidityTransitionRow.candle_index.asc(),
                    LiquidityTransitionRow.transitioned_at.asc(),
                    LiquidityTransitionRow.transition_id.asc(),
                )
            )

            rows = result.scalars().all()

            return tuple(
                LiquidityEvidenceRecord(
                    pool_id=row.pool_id,
                    from_state=row.from_state,
                    to_state=row.to_state,
                    reason=row.reason,
                    transitioned_at=(row.transitioned_at),
                    candle_index=(row.candle_index),
                    evidence=row.evidence,
                )
                for row in rows
            )

    async def list_recent_sweeps(
        self,
        *,
        limit: int,
    ) -> tuple[RecentSweepRecord, ...]:
        """The platform's latest consumed levels, newest first (§18.3).

        Terminal consumption states only: SWEPT and its stop-hunt cousin.
        BROKEN is a level absorbed by structure and EXPIRED is bookkeeping --
        neither is the "what got taken lately" a dashboard reader is asking.

        The side comes from an outer join to the pool: a transition whose pool
        row is gone (truncated, or from an older algo version) still lists,
        with `side` as None rather than the row silently vanishing -- the
        transitions table is append-only precisely so the record outlives the
        object.
        """
        async with self._sessions() as session:
            result = await session.execute(
                select(LiquidityTransitionRow, LiquidityPoolRow.side)
                .join(
                    LiquidityPoolRow,
                    LiquidityPoolRow.pool_id == LiquidityTransitionRow.pool_id,
                    isouter=True,
                )
                .where(LiquidityTransitionRow.to_state.in_(("SWEPT", "STOP_HUNT")))
                .order_by(
                    LiquidityTransitionRow.transitioned_at.desc(),
                    LiquidityTransitionRow.transition_id.desc(),
                )
                .limit(limit)
            )

            return tuple(
                RecentSweepRecord(
                    symbol=row.symbol,
                    timeframe=Timeframe(row.timeframe),
                    pool_id=row.pool_id,
                    side=side,
                    to_state=row.to_state,
                    reason=row.reason,
                    transitioned_at=row.transitioned_at,
                    evidence=row.evidence,
                )
                for row, side in result.all()
            )
