"""Create liquidity-detection persistence tables (Sprint S5).

Revision ID: 005_liquidity_detection
Revises: 004_detection_schema
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005_liquidity_detection"
down_revision = "004_detection_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create T14 liquidity pools and T15 transitions."""

    op.create_table(
        "liquidity_pools",
        sa.Column(
            "pool_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "symbol",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "timeframe",
            sa.String(length=8),
            nullable=False,
        ),
        sa.Column(
            "side",
            sa.String(length=8),
            nullable=False,
        ),
        sa.Column(
            "liquidity_class",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "price",
            sa.Numeric(
                precision=38,
                scale=18,
            ),
            nullable=False,
        ),
        sa.Column(
            "band_low",
            sa.Numeric(
                precision=38,
                scale=18,
            ),
            nullable=False,
        ),
        sa.Column(
            "band_high",
            sa.Numeric(
                precision=38,
                scale=18,
            ),
            nullable=False,
        ),
        sa.Column(
            "strength",
            sa.Numeric(
                precision=10,
                scale=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "member_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_index",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.Text(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "pool_id",
            name="pk_liquidity_pools",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_liquidity_pools_context_state",
        "liquidity_pools",
        [
            "symbol",
            "timeframe",
            "state",
        ],
        schema="detection",
    )

    op.create_index(
        "ix_liquidity_pools_strength",
        "liquidity_pools",
        [
            "symbol",
            "timeframe",
            "strength",
        ],
        schema="detection",
    )

    op.create_table(
        "liquidity_transitions",
        sa.Column(
            "transition_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "pool_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "symbol",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "timeframe",
            sa.String(length=8),
            nullable=False,
        ),
        sa.Column(
            "from_state",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "to_state",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "candle_index",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.Text(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "transition_id",
            name="pk_liquidity_transitions",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_liquidity_transitions_pool_time",
        "liquidity_transitions",
        [
            "pool_id",
            "transitioned_at",
        ],
        schema="detection",
    )

    op.create_index(
        "ix_liquidity_transitions_context_time",
        "liquidity_transitions",
        [
            "symbol",
            "timeframe",
            "transitioned_at",
        ],
        schema="detection",
    )


def downgrade() -> None:
    """Remove Sprint S5 liquidity persistence."""

    op.drop_index(
        "ix_liquidity_transitions_context_time",
        table_name="liquidity_transitions",
        schema="detection",
    )

    op.drop_index(
        "ix_liquidity_transitions_pool_time",
        table_name="liquidity_transitions",
        schema="detection",
    )

    op.drop_table(
        "liquidity_transitions",
        schema="detection",
    )

    op.drop_index(
        "ix_liquidity_pools_strength",
        table_name="liquidity_pools",
        schema="detection",
    )

    op.drop_index(
        "ix_liquidity_pools_context_state",
        table_name="liquidity_pools",
        schema="detection",
    )

    op.drop_table(
        "liquidity_pools",
        schema="detection",
    )
