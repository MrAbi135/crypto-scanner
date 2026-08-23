"""PostgreSQL persistence for SLS §5.9 zone interactions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

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
from scanner.domain.ict import TERMINAL_ZONE_STATES, InteractionKind
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

    async def append_many(
        self,
        interactions: Sequence[IctZoneInteractionRecord],
    ) -> frozenset[str]:
        if not interactions:
            return frozenset()

        stmt = (
            pg_insert(IctZoneInteractionRow)
            .values(
                [
                    {
                        "interaction_id": item.interaction_id,
                        "zone_id": item.zone_id,
                        "symbol": item.symbol,
                        "timeframe": item.timeframe.value,
                        "zone_type": item.zone_type,
                        "kind": item.kind,
                        "observed_at": item.observed_at,
                        "candle_index": item.candle_index,
                        "penetration_depth": item.penetration_depth,
                        "close_price": item.close_price,
                        "rejection_wick": item.rejection_wick,
                        "close_through": item.close_through,
                        "evidence": item.evidence,
                    }
                    for item in interactions
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IctZoneInteractionRow.interaction_id,
                ]
            )
            .returning(IctZoneInteractionRow.interaction_id)
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)

            written = frozenset(result.scalars().all())

            await session.commit()

            return written

    async def any_respect_at(
        self,
        symbol: str,
        timeframe: Timeframe,
        observed_at: datetime,
    ) -> bool:
        """Existence only. §6.5 asks whether the candle qualifies, not which
        zone made it qualify, and `(symbol, timeframe, observed_at)` is the
        index this table already carries."""
        async with self._sessions() as session:
            found = (
                await session.execute(
                    select(IctZoneInteractionRow.interaction_id)
                    .where(
                        IctZoneInteractionRow.symbol == symbol,
                        IctZoneInteractionRow.timeframe == timeframe.value,
                        IctZoneInteractionRow.observed_at == observed_at,
                        IctZoneInteractionRow.kind == InteractionKind.RESPECT.value,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

            return found is not None

    async def list_for_zone(
        self,
        zone_id: str,
    ) -> tuple[IctZoneInteractionRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneInteractionRow)
                .where(IctZoneInteractionRow.zone_id == zone_id)
                # Ordered so that "first retest" is answerable at all, and by
                # observed_at rather than candle_index: the latter is the
                # offset inside whichever sliding window recorded the row, so
                # ordering by it sorted the history by an accident of when the
                # engine happened to look, not by when the interactions
                # occurred. The interaction_id tie-break keeps two interactions
                # on one candle in a stable order rather than whatever the
                # planner returns.
                .order_by(
                    IctZoneInteractionRow.observed_at.asc(),
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
                    # §5 makes terminal states permanent, so these zones can
                    # never interact again. Returning them had the interaction
                    # replay walk 3,934 zones on real BTCUSDT H1 where 701 were
                    # still capable of anything -- five sixths of the largest
                    # service's work, on zones that were dead.
                    IctZoneRow.state.notin_(sorted(TERMINAL_ZONE_STATES)),
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

    async def list_transitions_for(
        self,
        zone_ids: Sequence[str],
    ) -> dict[str, tuple[IctZoneTransitionRecord, ...]]:
        if not zone_ids:
            return {}

        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneTransitionRow)
                .where(
                    IctZoneTransitionRow.zone_id.in_(list(zone_ids)),
                )
                .order_by(
                    IctZoneTransitionRow.zone_id.asc(),
                    IctZoneTransitionRow.candle_index.asc(),
                    IctZoneTransitionRow.transitioned_at.asc(),
                    IctZoneTransitionRow.transition_id.asc(),
                )
            )

            grouped: dict[str, list[IctZoneTransitionRecord]] = {}

            for row in result.scalars().all():
                grouped.setdefault(row.zone_id, []).append(_transition_record(row))

            # Every requested id gets an entry, so a caller can index without
            # having to distinguish "no transitions" from "not asked for".
            return {zone_id: tuple(grouped.get(zone_id, ())) for zone_id in zone_ids}


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
