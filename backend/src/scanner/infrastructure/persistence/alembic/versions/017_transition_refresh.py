"""§10.3's refresh events, recorded on T18 beside the stress tests.

§10.3: *"A signal matching an ACTIVE signal's key is merged as a refresh event
on the existing signal (evidence appended) — never a second alert."* §12.1
says the same from the other side: "immutable core: evidence, zones, levels
never mutate post-creation (**refresh events append**)".

They append here rather than to a table of their own, because T18 already
holds observations that do not move a signal — its own description covers
"`stress_test` wick events" the same way. A refresh is that shape exactly: a
fact about the signal at one candle, with `from_state == to_state`.

**The unique key has to widen.** T18 was unique on `(signal_id, candle)`
because §12 monitors once per closed candle and one candle gets one verdict.
A refresh is written by the *detector*, not the monitor, and the two run on
the same closed candle — so a signal that both moved and was re-detected on
one candle would have had one of the two facts silently swallowed by the
constraint. The key now separates them, which keeps "one verdict per candle"
and "one refresh per candle" as two rules rather than one rule that loses
data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017_transition_refresh"
down_revision = "016_signal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signal_transitions",
        sa.Column("refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="detection",
    )

    # The default served the existing rows; leaving it would let a future
    # insert omit the column and land as "not a refresh" without saying so.
    op.alter_column(
        "signal_transitions",
        "refresh",
        server_default=None,
        schema="detection",
    )

    op.drop_constraint(
        "uq_signal_transitions_signal_candle",
        "signal_transitions",
        schema="detection",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_signal_transitions_signal_candle",
        "signal_transitions",
        ["signal_id", "at_candle_open_time", "refresh"],
        schema="detection",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_signal_transitions_signal_candle",
        "signal_transitions",
        schema="detection",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_signal_transitions_signal_candle",
        "signal_transitions",
        ["signal_id", "at_candle_open_time"],
        schema="detection",
    )

    op.drop_column("signal_transitions", "refresh", schema="detection")
