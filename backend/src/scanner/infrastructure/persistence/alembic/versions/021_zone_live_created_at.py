"""Index the live-zone reads on the column they actually order by.

Both bounded zone reads — `PgIctZoneRepository.list_live` and
`PgIctZoneInteractionContextRepository.list_zones` — filter on
`(symbol, timeframe, non-terminal state)` and order by `created_at DESC` with
`LIMIT 60`. The partial index they had was on `created_index`, which is the
zone's offset inside whichever 500-candle window first detected it and is no
longer what either query sorts by.

So the filter used the index and the sort did not: PostgreSQL fetched every
live zone for the context and sorted it to return sixty. On the soak VM that
was 33,807 rows per read, twice per pass, and it is a large part of why a pass
cost 82 seconds against §14's 2-second target.

With the ordering in the index the plan is an index scan that stops after
sixty rows.

**The old index is dropped in the same migration.** Nothing orders by
`created_index` any more — it was removed from `list_live` earlier and from
`list_zones` in the change this accompanies. Keeping it would cost a write on
every zone insert to serve no read. The new index covers the same filter, so
the queries that only filtered are no worse off.

Both statements are `CONCURRENTLY`: `ict_zones` carries half a million rows on
the staging host and the engine reads it on every close, so a plain
`CREATE INDEX` would take an ACCESS EXCLUSIVE lock and stall detection for the
duration.

`CONCURRENTLY` cannot run inside a transaction, and Alembic wraps each
migration in one. `op.get_context().autocommit_block()` is the mechanism —
*not* a `disable_ddl_transaction` module flag, which is Django's and which
Alembic silently ignores. The first version of this file had the flag and
failed on every integration test with "cannot run inside a transaction block".
"""

from __future__ import annotations

from alembic import op

revision = "021_zone_live_created_at"
down_revision = "020_sessions"
branch_labels = None
depends_on = None

# Must match the queries' filter exactly, or the planner will not use the
# partial index -- and the symptom is silent: everything works, slowly.
_LIVE = "state NOT IN ('INVALIDATED','EXPIRED','FILLED','INVERTED','DEAD')"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ict_zones_live_recent
            ON detection.ict_zones (symbol, timeframe, created_at DESC)
            WHERE {_LIVE}
            """
        )

        op.execute("DROP INDEX CONCURRENTLY IF EXISTS detection.ix_ict_zones_live_context")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ict_zones_live_context
            ON detection.ict_zones (symbol, timeframe, created_index)
            WHERE {_LIVE}
            """
        )

        op.execute("DROP INDEX CONCURRENTLY IF EXISTS detection.ix_ict_zones_live_recent")
