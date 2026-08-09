"""Persist daily liquidity history for universe tiering (Sprint S3).

Revision ID: 003_liquidity_history
Revises: 002_universe_state
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_liquidity_history"
down_revision = "002_universe_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create daily liquidity history table."""

    op.create_table(
        "liquidity_history",
        sa.Column(
            "exchange_symbol",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "daily_quote_volume",
            sa.Numeric(),
            nullable=False,
        ),
        sa.Column(
            "spread_bps",
            sa.Numeric(),
            nullable=False,
        ),
        sa.Column(
            "depth_2pct",
            sa.Numeric(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "exchange_symbol",
            "observed_at",
            name="pk_liquidity_history",
        ),
        sa.CheckConstraint(
            "daily_quote_volume >= 0",
            name="ck_liquidity_history_volume_nonnegative",
        ),
        sa.CheckConstraint(
            "spread_bps >= 0",
            name="ck_liquidity_history_spread_nonnegative",
        ),
        sa.CheckConstraint(
            "depth_2pct >= 0",
            name="ck_liquidity_history_depth_nonnegative",
        ),
        schema="market",
    )

    op.create_index(
        "ix_liquidity_history_symbol_observed_at",
        "liquidity_history",
        [
            "exchange_symbol",
            "observed_at",
        ],
        schema="market",
    )


def downgrade() -> None:
    """Drop daily liquidity history table."""

    op.drop_index(
        "ix_liquidity_history_symbol_observed_at",
        table_name="liquidity_history",
        schema="market",
    )

    op.drop_table(
        "liquidity_history",
        schema="market",
    )
