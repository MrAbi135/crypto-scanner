"""Create the transactional outbox (T39, TAD §12).

Events are written in the same transaction as the business fact that caused
them, then relayed to Redis Streams and marked relayed. That co-transactional
write is the whole point: a candle cannot exist without its event, and an event
cannot exist for a candle that rolled back.

Revision ID: 008_outbox_events
Revises: 007_ict_zone_interactions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008_outbox_events"
down_revision = "007_ict_zone_interactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")

    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "aggregate_type",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "aggregate_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "event_type",
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
        sa.Column(
            "relayed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "relay_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.CheckConstraint(
            "relay_attempts >= 0",
            name="ck_outbox_events_attempts_non_negative",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_outbox_events",
        ),
        schema="ops",
    )

    # The relay's work queue. Partial on unrelayed rows because the relayed set
    # is the one that grows without bound until the 7-day prune (DDD §retention)
    # -- indexing it would mean maintaining an index nobody reads.
    op.create_index(
        "ix_outbox_events_unrelayed",
        "outbox_events",
        [
            "created_at",
        ],
        unique=False,
        postgresql_where=sa.text("relayed_at IS NULL"),
        schema="ops",
    )

    # Pruning reads by relayed_at; without this the 7-day sweep is a seq scan
    # over the whole table every night.
    op.create_index(
        "ix_outbox_events_relayed_at",
        "outbox_events",
        [
            "relayed_at",
        ],
        unique=False,
        postgresql_where=sa.text("relayed_at IS NOT NULL"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_events_relayed_at",
        table_name="outbox_events",
        schema="ops",
    )

    op.drop_index(
        "ix_outbox_events_unrelayed",
        table_name="outbox_events",
        schema="ops",
    )

    op.drop_table(
        "outbox_events",
        schema="ops",
    )
