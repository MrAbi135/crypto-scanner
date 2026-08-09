"""Persist universe tier hysteresis state (Sprint S3).

Revision ID: 002_universe_state
Revises: 001_market_schema
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002_universe_state"
down_revision = "001_market_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add persistent universe tier and hysteresis state to symbols."""

    op.add_column(
        "symbols",
        sa.Column(
            "tier",
            sa.String(length=16),
            nullable=False,
            server_default="INELIGIBLE",
        ),
        schema="market",
    )

    op.add_column(
        "symbols",
        sa.Column(
            "candidate_tier",
            sa.String(length=16),
            nullable=True,
        ),
        schema="market",
    )

    op.add_column(
        "symbols",
        sa.Column(
            "consecutive_passes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="market",
    )

    op.add_column(
        "symbols",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="market",
    )

    op.create_check_constraint(
        "ck_symbols_tier",
        "symbols",
        "tier IN ('T1','T2','T3','INELIGIBLE')",
        schema="market",
    )

    op.create_check_constraint(
        "ck_symbols_candidate_tier",
        "symbols",
        (
            "candidate_tier IS NULL OR "
            "candidate_tier IN ('T1','T2','T3','INELIGIBLE')"
        ),
        schema="market",
    )

    op.create_check_constraint(
        "ck_symbols_consecutive_passes_nonnegative",
        "symbols",
        "consecutive_passes >= 0",
        schema="market",
    )

    op.create_check_constraint(
        "ck_symbols_consecutive_failures_nonnegative",
        "symbols",
        "consecutive_failures >= 0",
        schema="market",
    )


def downgrade() -> None:
    """Remove persistent universe tier and hysteresis state."""

    op.drop_constraint(
        "ck_symbols_consecutive_failures_nonnegative",
        "symbols",
        schema="market",
        type_="check",
    )
    op.drop_constraint(
        "ck_symbols_consecutive_passes_nonnegative",
        "symbols",
        schema="market",
        type_="check",
    )
    op.drop_constraint(
        "ck_symbols_candidate_tier",
        "symbols",
        schema="market",
        type_="check",
    )
    op.drop_constraint(
        "ck_symbols_tier",
        "symbols",
        schema="market",
        type_="check",
    )

    op.drop_column(
        "symbols",
        "consecutive_failures",
        schema="market",
    )
    op.drop_column(
        "symbols",
        "consecutive_passes",
        schema="market",
    )
    op.drop_column(
        "symbols",
        "candidate_tier",
        schema="market",
    )
    op.drop_column(
        "symbols",
        "tier",
        schema="market",
    )
    