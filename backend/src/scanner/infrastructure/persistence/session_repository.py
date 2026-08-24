"""PostgreSQL persistence for T22 `identity.sessions`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.sessions import RevokeReason, SessionRecord
from scanner.infrastructure.persistence.session_models import SessionRow


def _record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        user_id=row.user_id,
        refresh_hash=row.refresh_hash,
        issued_at=row.issued_at,
        rotated_at=row.rotated_at,
        expires_at=row.expires_at,
        rotation_count=row.rotation_count,
        device_label=row.device_label,
        ip_created=row.ip_created,
        revoked_at=row.revoked_at,
        revoke_reason=row.revoke_reason,
    )


class PgSessionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, session: SessionRecord) -> bool:
        stmt = (
            pg_insert(SessionRow)
            .values(
                session_id=session.session_id,
                user_id=session.user_id,
                refresh_hash=session.refresh_hash,
                issued_at=session.issued_at,
                rotated_at=session.rotated_at,
                expires_at=session.expires_at,
                rotation_count=session.rotation_count,
                device_label=session.device_label,
                ip_created=session.ip_created,
            )
            # No `index_elements`: the primary key and the unique refresh hash
            # must both refuse, and naming one would surface the other as a
            # driver error the caller cannot tell from an outage.
            .on_conflict_do_nothing()
            .returning(SessionRow.session_id)
        )

        async with self._sessions() as db:
            written = (await db.execute(stmt)).scalar_one_or_none()

            await db.commit()

            return written is not None

    async def get(self, session_id: str) -> SessionRecord | None:
        async with self._sessions() as db:
            row = await db.get(SessionRow, session_id)

        return _record(row) if row is not None else None

    async def rotate(
        self,
        session_id: str,
        *,
        expected_hash: str,
        new_hash: str,
        rotated_at: datetime,
    ) -> bool:
        """Compare-and-set, in one statement.

        The `refresh_hash == expected_hash` predicate is what makes two
        simultaneous refreshes of one valid token resolve to a single winner.
        Read-then-write would let both pass their check and both write,
        handing out two live tokens for one family -- and the loser's holder
        would then trip the reuse alarm, revoking a family nobody attacked.

        `revoked_at IS NULL` sits in the same predicate rather than in a check
        before it, so a family revoked between the read and the write cannot
        be rotated back into use.
        """
        stmt = (
            update(SessionRow)
            .where(
                SessionRow.session_id == session_id,
                SessionRow.refresh_hash == expected_hash,
                SessionRow.revoked_at.is_(None),
            )
            .values(
                refresh_hash=new_hash,
                rotated_at=rotated_at,
                rotation_count=SessionRow.rotation_count + 1,
            )
            .returning(SessionRow.session_id)
        )

        async with self._sessions() as db:
            written = (await db.execute(stmt)).scalar_one_or_none()

            await db.commit()

            return written is not None

    async def revoke(
        self,
        session_id: str,
        *,
        reason: RevokeReason,
        revoked_at: datetime,
    ) -> bool:
        """First reason wins.

        `revoked_at IS NULL` in the predicate makes this idempotent and keeps
        the original reason: a stale token replayed against a family the user
        logged out of must not overwrite `logout` with `reuse_detected`, or
        every forgotten tab would read as a theft in the audit trail.
        """
        stmt = (
            update(SessionRow)
            .where(SessionRow.session_id == session_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=revoked_at, revoke_reason=reason.value)
            .returning(SessionRow.session_id)
        )

        async with self._sessions() as db:
            written = (await db.execute(stmt)).scalar_one_or_none()

            await db.commit()

            return written is not None

    async def revoke_all_for_user(
        self,
        user_id: str,
        *,
        reason: RevokeReason,
        revoked_at: datetime,
    ) -> int:
        stmt = (
            update(SessionRow)
            .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=revoked_at, revoke_reason=reason.value)
            .returning(SessionRow.session_id)
        )

        async with self._sessions() as db:
            ended = (await db.execute(stmt)).scalars().all()

            await db.commit()

            return len(ended)

    async def list_live_for_user(
        self,
        user_id: str,
        *,
        now: datetime,
    ) -> tuple[SessionRecord, ...]:
        """Live families, newest activity first.

        Expiry is filtered as well as revocation. A family past `expires_at`
        is not revoked -- nothing writes a row for the clock passing -- so a
        query on `revoked_at IS NULL` alone would show dead sessions as active
        in §18.1's session view.
        """
        stmt = (
            select(SessionRow)
            .where(
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
            .order_by(SessionRow.rotated_at.desc(), SessionRow.session_id.desc())
        )

        async with self._sessions() as db:
            rows = (await db.execute(stmt)).scalars().all()

        return tuple(_record(row) for row in rows)
