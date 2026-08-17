"""Outbox repository (DDD T39).

Reads the relay's work queue. The write half lives in `PgCandleRepository`,
because an outbox row must be written inside the transaction of the fact that
caused it -- see `application/ports/outbox.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.outbox import OutboxRecord


class PgOutboxRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def claim_unrelayed(
        self,
        limit: int,
    ) -> Sequence[OutboxRecord]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        async with self._sessions() as session:
            # Ordered by id, which is a monotonic ULID, so the queue drains in
            # commit order rather than in whatever order the planner returns.
            #
            # SKIP LOCKED makes a second relay instance safe rather than
            # useful: two relays will not deliver the same row twice, but they
            # will interleave, and the stream stops reflecting commit order.
            # Run one. The lock is a guard against an accident, not a design
            # for horizontal scale.
            rows = await session.execute(
                text(
                    """
                    SELECT id,
                           aggregate_type,
                           aggregate_id,
                           event_type,
                           payload,
                           created_at,
                           relay_attempts
                    FROM ops.outbox_events
                    WHERE relayed_at IS NULL
                    ORDER BY id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"limit": limit},
            )

            claimed = [
                OutboxRecord(
                    id=row.id,
                    aggregate_type=row.aggregate_type,
                    aggregate_id=row.aggregate_id,
                    event_type=row.event_type,
                    payload=row.payload,
                    created_at=row.created_at,
                    relay_attempts=row.relay_attempts,
                )
                for row in rows
            ]

            # The row locks end here. Holding them across the Redis write would
            # mean an unreachable Redis pins a Postgres transaction open for as
            # long as the timeout, and the outbox already makes redelivery safe.
            await session.commit()

            return tuple(claimed)

    async def mark_relayed(
        self,
        ids: Sequence[str],
        *,
        relayed_at: datetime,
    ) -> int:
        if not ids:
            return 0

        async with self._sessions() as session:
            # `relayed_at IS NULL` keeps this idempotent: a retry after a
            # crash between the stream write and this update must not rewrite
            # the timestamp of an event that was already accounted for.
            result = await session.execute(
                text(
                    """
                    UPDATE ops.outbox_events
                    SET relayed_at = :relayed_at
                    WHERE id = ANY(:ids)
                      AND relayed_at IS NULL
                    """
                ),
                {
                    "relayed_at": relayed_at,
                    "ids": list(ids),
                },
            )

            await session.commit()

            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def record_relay_failure(
        self,
        ids: Sequence[str],
    ) -> int:
        if not ids:
            return 0

        async with self._sessions() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE ops.outbox_events
                    SET relay_attempts = relay_attempts + 1
                    WHERE id = ANY(:ids)
                      AND relayed_at IS NULL
                    """
                ),
                {"ids": list(ids)},
            )

            await session.commit()

            return int(result.rowcount or 0)  # type: ignore[attr-defined]
