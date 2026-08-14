"""Create detection persistence schema (Sprint S4).

Revision ID: 004_detection_schema
Revises: 003_liquidity_history
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004_detection_schema"
down_revision = "003_liquidity_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create detection schema and S4 persistence tables."""

    op.execute("CREATE SCHEMA IF NOT EXISTS detection")

    op.create_table(
        "algo_versions",
        sa.Column(
            "id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "engine",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_algo_versions",
        ),
        sa.UniqueConstraint(
            "engine",
            "version",
            name="uq_algo_versions_engine_version",
        ),
        schema="detection",
    )

    op.create_table(
        "engine_events",
        sa.Column(
            "event_key",
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
            "event_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "event_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "algo_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "event_key",
            "event_at",
            name="pk_engine_events",
        ),
        schema="detection",
    )

    op.create_index(
        "ix_engine_events_context_time",
        "engine_events",
        [
            "symbol",
            "timeframe",
            "event_at",
        ],
        schema="detection",
    )

    op.execute(
        """
        SELECT create_hypertable(
            'detection.engine_events',
            'event_at',
            if_not_exists => TRUE,
            migrate_data => TRUE
        )
        """
    )


def downgrade() -> None:
    """Remove S4 detection persistence."""

    op.drop_index(
        "ix_engine_events_context_time",
        table_name="engine_events",
        schema="detection",
    )

    op.drop_table(
        "engine_events",
        schema="detection",
    )

    op.drop_table(
        "algo_versions",
        schema="detection",
    )

    op.execute("DROP SCHEMA IF EXISTS detection")
