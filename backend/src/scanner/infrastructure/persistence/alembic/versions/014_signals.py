"""T17 `detection.signals` — the published signal record.

DDD T17: *"THE published signal record — the product's crown jewel. One row
per published signal: complete SLS §15.2 payload **snapshotted** ... as sealed
JSONB + extracted queryable columns; payload hash. **Append-only forever; no
UPDATE surface exists.**"*

**One index departs from the DDD, and it has to.** T17's index requirements
ask for a *"unique partial on dedup_key for active window (SLS §10.3)"*. A
partial index needs a predicate over a column of this table, and "active" is
not one: §12's state lives in T18's transitions, and T17 has no UPDATE surface
to record a change into. There is no expression over `published_at` that means
"still inside its TTL" either — §12.5's TTL is counted in candles and differs
per timeframe.

So the index here is a plain `(dedup_key, published_at)` one, and the
uniqueness it was meant to enforce is checked at write time instead. That is
where §15.3 already puts it: check (4) is "dedup key clear (§10.3)", evaluated
with the other four "exactly once, atomically" (§12.2). The index makes that
query cheap; the database cannot make it a constraint without a mutable
column, and adding one would cost the immutability the table exists for.

**Worth a ruling.** Either the DDD's index requirement is amended to match, or
T17 gains a closed-at column and stops being append-only. I have taken the
first reading because immutability is the stronger promise — Constitution
§45.5 calls it constitutional — but the document should say which.

Naming follows the detection schema as migrations 012 and 013 did: `symbol`
and `algo_version` as plain columns rather than T17's `symbol_id` and
`algo_version_id`, because no table in `detection` uses foreign keys for
either.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014_signals"
down_revision = "013_param_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(length=160), nullable=False),
        sa.Column("setup_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("archetype", sa.String(length=4), nullable=False),
        sa.Column("grade", sa.String(length=2), nullable=False),
        sa.Column("final_confidence", sa.Numeric(precision=38, scale=18), nullable=False),
        # §15.2's priced rows, extracted for querying. The sealed payload
        # carries them too; these exist so a board does not have to parse
        # JSON to draw a level.
        sa.Column("entry_proximal", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("entry_distal", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("invalidation_level", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("target_bands", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_candles", sa.Integer(), nullable=False),
        sa.Column("algo_version", sa.String(length=64), nullable=False),
        sa.Column("param_set_version", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("signal_id", name="pk_signals"),
        sa.CheckConstraint("direction IN ('UP','DOWN')", name="ck_signals_direction"),
        sa.CheckConstraint(
            "archetype IN ('A1','A2','A3','A4','A5')",
            name="ck_signals_archetype",
        ),
        # §9.4's bands. A published signal always has one -- §8.6 refuses to
        # publish below the lowest floor of 70, and 70 is grade B.
        sa.CheckConstraint("grade IN ('S','A','B')", name="ck_signals_grade"),
        sa.CheckConstraint("ttl_candles > 0", name="ck_signals_ttl"),
        # §15.3(1): "entry != invalidation side". The band itself cannot be
        # inverted either -- proximal and distal are oriented by direction, so
        # equality is the only thing both directions forbid.
        sa.CheckConstraint(
            "entry_proximal <> entry_distal",
            name="ck_signals_entry_band",
        ),
        schema="detection",
    )

    op.create_index("ix_signals_published_at", "signals", ["published_at"], schema="detection")

    op.create_index(
        "ix_signals_symbol_published",
        "signals",
        ["symbol", "published_at"],
        schema="detection",
    )

    op.create_index(
        "ix_signals_stats",
        "signals",
        ["archetype", "grade", "published_at"],
        schema="detection",
    )

    # Not unique -- see the module docstring. This serves §15.3(4)'s
    # "dedup key clear" lookup, which asks for the most recent signal on a key
    # and whether it is still inside its TTL.
    op.create_index(
        "ix_signals_dedup_key",
        "signals",
        ["dedup_key", "published_at"],
        schema="detection",
    )


def downgrade() -> None:
    for name in (
        "ix_signals_dedup_key",
        "ix_signals_stats",
        "ix_signals_symbol_published",
        "ix_signals_published_at",
    ):
        op.drop_index(name, table_name="signals", schema="detection")

    op.drop_table("signals", schema="detection")
