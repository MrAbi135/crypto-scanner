"""T18 `detection.signal_transitions` — §12's lifecycle history.

DDD T18: *"Lifecycle history per SLS §12 state machine
(PUBLISHED→ACTIVE→…), append-only. One row per transition with triggering
candle and premise-check evidence; includes `stress_test` wick events."*

**A signal's current state is the latest row here, not a column on T17.** T17
is append-only forever and has no UPDATE surface, so the state has to live
somewhere that grows. That is what makes this table load-bearing rather than
an audit nicety: without it nothing knows whether a signal is still live.

**`stress_test` rows carry no state change.** §12.3 records a wick through the
invalidation as a fact about the candle while the signal stays where it is, so
`from_state` and `to_state` are equal on those rows. The alternative -- a
nullable `to_state` -- would make every reader handle a null before it could
ask the only question that matters, and the answer is the same either way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015_signal_transitions"
down_revision = "014_signals"
branch_labels = None
depends_on = None

_STATES = (
    "'DETECTED','PUBLISHED','SUPPRESSED','ACTIVE','SUCCESS','FAILED',"
    "'EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"
)


def upgrade() -> None:
    op.create_table(
        "signal_transitions",
        sa.Column("transition_id", sa.String(length=160), nullable=False),
        sa.Column("signal_id", sa.String(length=160), nullable=False),
        sa.Column("from_state", sa.String(length=24), nullable=False),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("at_candle_open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stress_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_evidence", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("transition_id", name="pk_signal_transitions"),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["detection.signals.signal_id"],
            name="fk_signal_transitions_signal",
        ),
        sa.CheckConstraint(f"from_state IN ({_STATES})", name="ck_signal_transitions_from"),
        sa.CheckConstraint(f"to_state IN ({_STATES})", name="ck_signal_transitions_to"),
        # One transition per signal per candle. A second reading of the same
        # close is a replay, not a new fact, and §12's monitoring is "per
        # closed candle" -- one candle, one verdict.
        sa.UniqueConstraint(
            "signal_id",
            "at_candle_open_time",
            name="uq_signal_transitions_signal_candle",
        ),
        schema="detection",
    )

    # The reader that matters: a signal's history in order, and therefore its
    # current state as the last row.
    op.create_index(
        "ix_signal_transitions_signal_time",
        "signal_transitions",
        ["signal_id", "at_candle_open_time"],
        schema="detection",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_signal_transitions_signal_time",
        table_name="signal_transitions",
        schema="detection",
    )

    op.drop_table("signal_transitions", schema="detection")
