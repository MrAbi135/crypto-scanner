"""PostgreSQL persistence for SLS §5.9 zone interactions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionRecord,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.infrastructure.persistence.ict_zone_interaction_models import (
    IctZoneInteractionRow,
)
from scanner.infrastructure.persistence.ict_zone_models import (
    IctZoneRow,
    IctZoneTransitionRow,
)
from scanner.shared import Timeframe


class PgIctZoneInteractionRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def append(
        self,
        interaction: IctZoneInteractionRecord,
    ) -> bool:
        stmt = (
            pg_insert(IctZoneInteractionRow)
            .values(
                interaction_id=interaction.interaction_id,
                zone_id=interaction.zone_id,
                symbol=interaction.symbol,
                timeframe=interaction.timeframe.value,
                zone_type=interaction.zone_type,
                kind=interaction.kind,
                observed_at=interaction.observed_at,
                candle_index=interaction.candle_index,
                penetration_depth=interaction.penetration_depth,
                close_price=interaction.close_price,
                rejection_wick=interaction.rejection_wick,
                close_through=interaction.close_through,
                evidence=interaction.evidence,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IctZoneInteractionRow.interaction_id,
                ]
            )
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_for_zone(
        self,
        zone_id: str,
    ) -> tuple[IctZoneInteractionRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneInteractionRow)
                .where(IctZoneInteractionRow.zone_id == zone_id)
                # Ordered so that "first retest" is answerable at all. The
                # interaction_id tie-break keeps two interactions on one candle
                # in a stable order rather than whatever the planner returns.
                .order_by(
                    IctZoneInteractionRow.candle_index.asc(),
                    IctZoneInteractionRow.interaction_id.asc(),
                )
            )

            return tuple(
                IctZoneInteractionRecord(
                    interaction_id=row.interaction_id,
                    zone_id=row.zone_id,
                    symbol=row.symbol,
                    timeframe=Timeframe(row.timeframe),
                    zone_type=row.zone_type,
                    kind=row.kind,
                    observed_at=row.observed_at,
                    candle_index=row.candle_index,
                    penetration_depth=row.penetration_depth,
                    close_price=row.close_price,
                    rejection_wick=row.rejection_wick,
                    close_through=row.close_through,
                    evidence=row.evidence,
                )
                for row in result.scalars().all()
            )


class PgIctZoneInteractionContextRepository:
    """Read all zone facts plus their lifecycle boundaries."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def list_zones(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneRow)
                .where(
                    IctZoneRow.symbol == symbol,
                    IctZoneRow.timeframe == timeframe.value,
                )
                .order_by(
                    IctZoneRow.created_index.asc(),
                    IctZoneRow.zone_type.asc(),
                    IctZoneRow.zone_id.asc(),
                )
            )

            return tuple(_zone_record(row) for row in result.scalars().all())

    async def list_transitions(
        self,
        zone_id: str,
    ) -> tuple[IctZoneTransitionRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneTransitionRow)
                .where(
                    IctZoneTransitionRow.zone_id == zone_id,
                )
                .order_by(
                    IctZoneTransitionRow.candle_index.asc(),
                    IctZoneTransitionRow.transitioned_at.asc(),
                    IctZoneTransitionRow.transition_id.asc(),
                )
            )

            return tuple(_transition_record(row) for row in result.scalars().all())


def _zone_record(
    row: IctZoneRow,
) -> IctZoneRecord:
    return IctZoneRecord(
        zone_id=row.zone_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        zone_type=row.zone_type,
        polarity=row.polarity,
        state=row.state,
        grade=row.grade,
        band_low=row.band_low,
        band_high=row.band_high,
        refined_low=row.refined_low,
        refined_high=row.refined_high,
        created_index=row.created_index,
        confirmed_index=row.confirmed_index,
        created_at=row.created_at,
        updated_at=row.updated_at,
        parent_zone_id=row.parent_zone_id,
        dealing_range_id=row.dealing_range_id,
        stale_context=row.stale_context,
        gap_adjacent=row.gap_adjacent,
        origin_swept=row.origin_swept,
        evidence=row.evidence,
    )


def _transition_record(
    row: IctZoneTransitionRow,
) -> IctZoneTransitionRecord:
    return IctZoneTransitionRecord(
        transition_id=row.transition_id,
        zone_id=row.zone_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        zone_type=row.zone_type,
        from_state=row.from_state,
        to_state=row.to_state,
        reason=row.reason,
        transitioned_at=row.transitioned_at,
        candle_index=row.candle_index,
        evidence=row.evidence,
    )
