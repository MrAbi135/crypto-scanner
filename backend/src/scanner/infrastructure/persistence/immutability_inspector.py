"""PostgreSQL catalog reads behind the append-only boot check."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.immutability_verification import GUARDED_TABLES

# `tgenabled` is a char: 'O' origin, 'D' disabled, 'R' replica, 'A' always.
# Only 'D' means the trigger will not fire on a normal write, and the point of
# this query is to catch exactly that -- a trigger that exists in the catalog
# but has been switched off is what a naive existence check would call healthy.
_GUARD_COUNTS = text(
    """
    SELECT c.relname AS table_name, count(*) AS guards
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_proc p ON p.oid = t.tgfoid
    WHERE n.nspname = 'detection'
      AND NOT t.tgisinternal
      AND t.tgenabled <> 'D'
      AND p.proname = 'reject_mutation'
      AND c.relname = ANY(:tables)
    GROUP BY c.relname
    """
)

# Ownership is checked against the guarded tables rather than the database,
# because a table's owner can drop its triggers and rewrite its rows no matter
# what the database-level grants say.
_ROLE_BYPASSES = text(
    """
    SELECT
        bool_or(r.rolsuper) AS is_super,
        bool_or(pg_catalog.pg_get_userbyid(c.relowner) = current_user) AS owns_guarded
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN pg_roles r
    WHERE n.nspname = 'detection'
      AND c.relname = ANY(:tables)
      AND r.rolname = current_user
    """
)


class PgImmutabilityInspector:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def enabled_guard_counts(self) -> dict[str, int]:
        async with self._sessions() as session:
            rows = await session.execute(_GUARD_COUNTS, {"tables": list(GUARDED_TABLES)})

            return {row.table_name: row.guards for row in rows}

    async def connection_role_bypasses_grants(self) -> bool:
        async with self._sessions() as session:
            row = (
                await session.execute(_ROLE_BYPASSES, {"tables": list(GUARDED_TABLES)})
            ).one_or_none()

            if row is None:
                # No guarded table is visible to this role, which the guard
                # check above will already have refused. Reporting True here
                # keeps the two answers from disagreeing about a database
                # neither of them could read.
                return True

            return bool(row.is_super) or bool(row.owns_guarded)
