"""Create ICT-zone persistence tables (Sprint S6).

Revision ID: 006_ict_zones
Revises: 005_liquidity_detection
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006_ict_zones"
down_revision = "005_liquidity_detection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create T12 ICT zones and T13 zone transitions."""

    op.create_table(
        "ict_zones",
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
            "polarity",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "grade",
            sa.String(length=24),
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
            "refined_low",
            sa.Numeric(
                precision=38,
                scale=18,
            ),
            nullable=True,
        ),
        sa.Column(
            "refined_high",
            sa.Numeric(
                precision=38,
                scale=18,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_index",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "confirmed_index",
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
            "parent_zone_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "dealing_range_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "stale_context",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "gap_adjacent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "origin_swept",
            sa.Boolean(),
            nullable=True,
        ),
        sa.Column(
            "evidence",
            sa.Text(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "band_high >= band_low",
            name="ck_ict_zones_band",
        ),
        sa.CheckConstraint(
            "(refined_low IS NULL AND refined_high IS NULL) "
            "OR "
            "(refined_low IS NOT NULL "
            "AND refined_high IS NOT NULL "
            "AND refined_high >= refined_low)",
            name="ck_ict_zones_refined_band",
        ),
        sa.PrimaryKeyConstraint(
            "zone_id",
            name="pk_ict_zones",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_ict_zones_context",
        "ict_zones",
        [
            "symbol",
            "timeframe",
            "zone_type",
        ],
        schema="detection",
    )

    op.create_index(
        "ix_ict_zones_live_context",
        "ict_zones",
        [
            "symbol",
            "timeframe",
            "created_index",
        ],
        unique=False,
        schema="detection",
        postgresql_where=sa.text(
            "state NOT IN ('INVALIDATED', 'EXPIRED', 'FILLED', 'INVERTED', 'DEAD')"
        ),
    )

    op.create_table(
        "ict_zone_transitions",
        sa.Column(
            "transition_id",
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
            "from_state",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "to_state",
            sa.String(length=24),
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
            name="pk_ict_zone_transitions",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_transitions_zone_time",
        "ict_zone_transitions",
        [
            "zone_id",
            "transitioned_at",
        ],
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_transitions_context_time",
        "ict_zone_transitions",
        [
            "symbol",
            "timeframe",
            "transitioned_at",
        ],
        schema="detection",
    )


def downgrade() -> None:
    """Remove Sprint S6 ICT-zone persistence."""

    op.drop_index(
        "ix_ict_zone_transitions_context_time",
        table_name="ict_zone_transitions",
        schema="detection",
    )

    op.drop_index(
        "ix_ict_zone_transitions_zone_time",
        table_name="ict_zone_transitions",
        schema="detection",
    )

    op.drop_table(
        "ict_zone_transitions",
        schema="detection",
    )

    op.drop_index(
        "ix_ict_zones_live_context",
        table_name="ict_zones",
        schema="detection",
    )

    op.drop_index(
        "ix_ict_zones_context",
        table_name="ict_zones",
        schema="detection",
    )

    op.drop_table(
        "ict_zones",
        schema="detection",
    )
