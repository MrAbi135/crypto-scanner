"""§6.6's wash_risk tag on the symbol registry.

§6.6 is symbol-level and daily, with a hysteresis §6.6 itself points at:
"tag lifts after 3 consecutive clean days (hysteresis, §1.4 pattern)". §1.4's
pattern already lives on `market.symbols` as `consecutive_passes` /
`consecutive_failures` beside the tier, so this follows it rather than
inventing a second place for a symbol's daily state.

No new table: DDD names no home for the tag, and one boolean plus one counter
per symbol is not a time series.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010_wash_risk"
down_revision = "009_trade_aggregates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "symbols",
        sa.Column("wash_risk", sa.Boolean, nullable=False, server_default=sa.false()),
        schema="market",
    )
    op.add_column(
        "symbols",
        sa.Column("wash_clean_days", sa.Integer, nullable=False, server_default="0"),
        schema="market",
    )
    op.create_check_constraint(
        "ck_symbols_wash_clean_days",
        "symbols",
        # §6.6 lifts at three, so the counter never reaches it and survives.
        "wash_clean_days >= 0 AND wash_clean_days < 3",
        schema="market",
    )


def downgrade() -> None:
    op.drop_constraint("ck_symbols_wash_clean_days", "symbols", schema="market")
    op.drop_column("symbols", "wash_clean_days", schema="market")
    op.drop_column("symbols", "wash_risk", schema="market")
