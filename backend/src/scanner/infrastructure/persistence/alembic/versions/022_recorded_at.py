"""Record when each transition and interaction row was written.

`detection.engine_events` carries `created_at`, the moment a pass wrote the
row, beside `event_at`, the candle it is about -- which is what let the
look-ahead audit measure how late each fact was written and prove that whole
classes of them were back-written hundreds of candles after their candle.
The zone-transition, liquidity-transition and interaction tables carry only
the candle time (`transitioned_at`, `observed_at`), so the same question could
be answered for them only offline, by replaying the engine.

This adds `recorded_at` to those three tables so the post-deploy late-write
invariant can cover them on the host.

**Existing rows stay NULL.** The column is added without a default first --
a metadata-only change, no table rewrite -- and the `now()` default is set
afterwards, so it applies to new rows only. Adding it with the default in one
statement would stamp every existing row with the migration's own timestamp,
which is a write time that never happened. NULL says "not recorded", which is
the truth for rows written before this column existed.

No code change is needed on the write path: the repositories insert explicit
column lists, so the database default fills the column.
"""

from __future__ import annotations

from alembic import op

revision = "022_recorded_at"
down_revision = "021_zone_live_created_at"
branch_labels = None
depends_on = None

_TABLES = ("ict_zone_transitions", "liquidity_transitions", "ict_zone_interactions")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE detection.{table} ADD COLUMN IF NOT EXISTS recorded_at timestamptz"
        )
        op.execute(f"ALTER TABLE detection.{table} ALTER COLUMN recorded_at SET DEFAULT now()")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE detection.{table} DROP COLUMN IF EXISTS recorded_at")
