"""Create immutable ICT zone interaction facts (SLS §5.9).

Revision ID: 007_ict_zone_interactions
Revises: 006_ict_zones
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007_ict_zone_interactions"
down_revision = "006_ict_zones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ict_zone_interactions",
        sa.Column(
            "interaction_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
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
            "zone_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "candle_index",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "penetration_depth",
            sa.Numeric(38, 18),
            nullable=False,
        ),
        sa.Column(
            "close_price",
            sa.Numeric(38, 18),
            nullable=False,
        ),
        sa.Column(
            "rejection_wick",
            sa.Numeric(38, 18),
            nullable=False,
        ),
        sa.Column(
            "close_through",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.Text(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('TOUCH','REJECTION','MITIGATION','RESPECT','VIOLATION','CONFIRMATION')",
            name="ck_ict_zone_interactions_kind",
        ),
        sa.PrimaryKeyConstraint(
            "interaction_id",
            name="pk_ict_zone_interactions",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_interactions_zone_time",
        "ict_zone_interactions",
        [
            "zone_id",
            "observed_at",
        ],
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_interactions_context_time",
        "ict_zone_interactions",
        [
            "symbol",
            "timeframe",
            "observed_at",
        ],
        schema="detection",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ict_zone_interactions_context_time",
        table_name="ict_zone_interactions",
        schema="detection",
    )

    op.drop_index(
        "ix_ict_zone_interactions_zone_time",
        table_name="ict_zone_interactions",
        schema="detection",
    )

    op.drop_table(
        "ict_zone_interactions",
        schema="detection",
    )
