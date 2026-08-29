"""PostgreSQL repositories for Sprint S6 ICT zones."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.domain.ict import MAX_ZONES
from scanner.infrastructure.persistence.ict_zone_models import (
    IctZoneRow,
    IctZoneTransitionRow,
)
from scanner.shared import Timeframe

_TERMINAL_STATES = (
    "INVALIDATED",
    "EXPIRED",
    "FILLED",
    "INVERTED",
    "DEAD",
)


class PgIctZoneRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def upsert(
        self,
        zone: IctZoneRecord,
    ) -> None:
        stmt = pg_insert(IctZoneRow).values(
            zone_id=zone.zone_id,
            symbol=zone.symbol,
            timeframe=zone.timeframe.value,
            zone_type=zone.zone_type,
            polarity=zone.polarity,
            state=zone.state,
            grade=zone.grade,
            band_low=zone.band_low,
            band_high=zone.band_high,
            refined_low=zone.refined_low,
            refined_high=zone.refined_high,
            created_index=zone.created_index,
            confirmed_index=zone.confirmed_index,
            created_at=zone.created_at,
            updated_at=zone.updated_at,
            parent_zone_id=zone.parent_zone_id,
            dealing_range_id=zone.dealing_range_id,
            stale_context=zone.stale_context,
            gap_adjacent=zone.gap_adjacent,
            origin_swept=zone.origin_swept,
            evidence=zone.evidence,
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[
                IctZoneRow.zone_id,
            ],
            set_={
                "grade": stmt.excluded.grade,
                "band_low": stmt.excluded.band_low,
                "band_high": stmt.excluded.band_high,
                "refined_low": stmt.excluded.refined_low,
                "refined_high": stmt.excluded.refined_high,
                "updated_at": stmt.excluded.updated_at,
                "parent_zone_id": stmt.excluded.parent_zone_id,
                "dealing_range_id": stmt.excluded.dealing_range_id,
                "stale_context": stmt.excluded.stale_context,
                "gap_adjacent": stmt.excluded.gap_adjacent,
                "origin_swept": stmt.excluded.origin_swept,
                "evidence": stmt.excluded.evidence,
            },
            where=(~IctZoneRow.state.in_(_TERMINAL_STATES)),
        )

        async with self._sessions() as session:
            await session.execute(stmt)
            await session.commit()

    async def get(
        self,
        zone_id: str,
    ) -> IctZoneRecord | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneRow).where(IctZoneRow.zone_id == zone_id).limit(1)
            )

            row = result.scalar_one_or_none()

            if row is None:
                return None

            return _zone_record(row)

    async def list_live(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        only_versions: Mapping[str, str] | None = None,
    ) -> tuple[IctZoneRecord, ...]:
        """Live zones, optionally pinned to each type's current algo version.

        The filter runs in SQL, **before** the limit, deliberately: applied in
        Python afterwards, superseded rows would still occupy the strength-
        ordered top slots and evict current zones from the bounded answer --
        the reader would see fewer real zones because dead versions crowded
        the doorway.

        `None` means every version, which is what a lifecycle wants: it is
        the read that retires old rows, and pinning it would freeze them
        live forever. See `application/detection/zone_versions.py`.
        """
        conditions = [
            IctZoneRow.symbol == symbol,
            IctZoneRow.timeframe == timeframe.value,
            ~IctZoneRow.state.in_(_TERMINAL_STATES),
        ]

        if only_versions is not None:
            # evidence is a JSON text column; there is no algo_version column
            # (detection rows are self-contained evidence, DDD v1.0.1). The
            # live set per context is bounded, so the cast is cheap.
            version_of = sa.cast(IctZoneRow.evidence, postgresql.JSONB)["algo_version"].astext

            conditions.append(
                sa.or_(
                    *(
                        sa.and_(IctZoneRow.zone_type == zone_type, version_of == version)
                        for zone_type, version in sorted(only_versions.items())
                    )
                )
            )

        async with self._sessions() as session:
            result = await session.execute(
                select(IctZoneRow)
                .where(*conditions)
                # `created_at`, not `created_index`. The index is the zone's
                # offset inside whichever 500-candle window first detected it,
                # frozen there while the window slides on -- so ordering the
                # live set by it sorted zones from different windows against
                # each other by an accident of when the engine looked. That
                # matters far more now than it did as a display order, because
                # the limit below decides which zones §8 gets to see at all.
                .order_by(
                    IctZoneRow.created_at.desc(),
                    IctZoneRow.zone_type.asc(),
                    IctZoneRow.zone_id.asc(),
                )
                # §5.1's bound. Applied in the query rather than after it:
                # BTCUSDT M5 carries 9,463 live zones on the soak VM against a
                # stated 60, and every confluence pass read all of them.
                .limit(MAX_ZONES)
            )

            rows = result.scalars().all()

            return tuple(_zone_record(row) for row in rows)

    async def transition(
        self,
        zone_id: str,
        *,
        from_state: str,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        stmt = (
            update(IctZoneRow)
            .where(
                IctZoneRow.zone_id == zone_id,
                IctZoneRow.state == from_state,
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


class PgIctZoneTransitionRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def append(
        self,
        transition: IctZoneTransitionRecord,
    ) -> bool:
        stmt = (
            pg_insert(IctZoneTransitionRow)
            .values(
                transition_id=transition.transition_id,
                zone_id=transition.zone_id,
                symbol=transition.symbol,
                timeframe=transition.timeframe.value,
                zone_type=transition.zone_type,
                from_state=transition.from_state,
                to_state=transition.to_state,
                reason=transition.reason,
                transitioned_at=transition.transitioned_at,
                candle_index=transition.candle_index,
                evidence=transition.evidence,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IctZoneTransitionRow.transition_id,
                ]
            )
        )

        async with self._sessions() as session:
            result = await session.execute(stmt)
            await session.commit()

            return bool(result.rowcount)  # type: ignore[attr-defined]


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
