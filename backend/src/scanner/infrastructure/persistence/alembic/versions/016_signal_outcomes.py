"""T19 `detection.signal_outcomes` — the source of every public statistic.

DDD T19: *"Terminal results: SUCCESS/FAILED/EXPIRED classes + MFE/MAE in R,
elapsed candles. Exactly one row per resolved signal; the source of every
public statistic."* Insert-once, immutable, 1:1 with T17.

`signal_id` is the primary key, which is what "exactly one row per resolved
signal" means when written down: a second resolution cannot be inserted at
all, rather than being inserted and then noticed.

`excluded_from_stats` carries §1.7's delisting flag. It is a column rather
than a filter applied at read time because the reason a signal leaves the
statistics has to travel with it -- a query that excluded delisted symbols by
joining the universe would silently change historical numbers every time a
symbol's status changed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016_signal_outcomes"
down_revision = "015_signal_transitions"
branch_labels = None
depends_on = None

_OUTCOMES = "'SUCCESS','FAILED','EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"


def upgrade() -> None:
    op.create_table(
        "signal_outcomes",
        sa.Column("signal_id", sa.String(length=160), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_candles", sa.Integer(), nullable=False),
        sa.Column("mfe_r", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("mae_r", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column(
            "excluded_from_stats",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("resolution_evidence", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("signal_id", name="pk_signal_outcomes"),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["detection.signals.signal_id"],
            name="fk_signal_outcomes_signal",
        ),
        sa.CheckConstraint(f"outcome IN ({_OUTCOMES})", name="ck_signal_outcomes_outcome"),
        # §12.4 measures both as distances travelled, and a distance cannot be
        # negative. A negative here would mean the excursion was computed
        # against the wrong side of the entry.
        sa.CheckConstraint("mfe_r >= 0 AND mae_r >= 0", name="ck_signal_outcomes_excursions"),
        sa.CheckConstraint("elapsed_candles >= 0", name="ck_signal_outcomes_elapsed"),
        schema="detection",
    )

    op.create_index(
        "ix_signal_outcomes_stats",
        "signal_outcomes",
        ["outcome", "resolved_at"],
        schema="detection",
    )


def downgrade() -> None:
    op.drop_index("ix_signal_outcomes_stats", table_name="signal_outcomes", schema="detection")

    op.drop_table("signal_outcomes", schema="detection")
