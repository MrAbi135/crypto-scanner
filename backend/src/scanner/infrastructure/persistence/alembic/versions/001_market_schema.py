"""Sprint S1: market schema — symbols (T1), candles hypertable (T3),
data_incidents (T8). Timescale policies per DDD §13/§14.

Revision ID: 001_market_schema
Revises: 000_baseline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001_market_schema"
down_revision = "000_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS market")

    op.create_table(
        "symbols",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("exchange_symbol", sa.String(32), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(16), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("venue", "exchange_symbol", name="uq_symbols_venue_exchange"),
        sa.CheckConstraint(
            "status IN ('QUARANTINE','ACTIVE','DELISTING','DELISTED')", name="ck_symbols_status"
        ),
        schema="market",
    )
    op.create_index(
        "ix_symbols_status",
        "symbols",
        ["status"],
        schema="market",
        postgresql_where=sa.text("status IN ('ACTIVE','QUARANTINE')"),
    )

    op.create_table(
        "candles",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric, nullable=False),
        sa.Column("high", sa.Numeric, nullable=False),
        sa.Column("low", sa.Numeric, nullable=False),
        sa.Column("close", sa.Numeric, nullable=False),
        sa.Column("volume", sa.Numeric, nullable=False),
        sa.Column("quote_volume", sa.Numeric, nullable=False),
        sa.Column("taker_buy_volume", sa.Numeric, nullable=False),
        sa.Column("trade_count", sa.BigInteger, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_time", name="pk_candles"),
        sa.CheckConstraint("high >= low", name="ck_candles_hl"),
        sa.CheckConstraint(
            "high >= open AND high >= close AND low <= open AND low <= close",
            name="ck_candles_ohlc",
        ),
        sa.CheckConstraint(
            "volume >= 0 AND quote_volume >= 0 AND taker_buy_volume >= 0 "
            "AND taker_buy_volume <= volume AND trade_count >= 0",
            name="ck_candles_volumes",
        ),
        sa.CheckConstraint("timeframe IN ('M5','M15','H1','H4','D1','W1')", name="ck_candles_tf"),
        schema="market",
    )

    # Hypertable: 7-day chunks (DDD §13 — M5/M15 volume dictates the floor;
    # per-TF chunk tuning is a measured optimization, not a v1 guess).
    op.execute(
        "SELECT create_hypertable('market.candles', 'open_time', "
        "chunk_time_interval => INTERVAL '7 days', migrate_data => TRUE)"
    )
    # Compression: segment by series, order by time (DDD §14).
    op.execute(
        "ALTER TABLE market.candles SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol, timeframe', "
        "timescaledb.compress_orderby = 'open_time')"
    )
    op.execute("SELECT add_compression_policy('market.candles', INTERVAL '7 days')")
    # Retention: NONE on candles in S1 (DDD §11 — M5 downsampling lands with
    # the S3 continuous aggregates; deleting before the rollup exists would
    # destroy history the roadmap still needs).

    op.create_table(
        "data_incidents",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("timeframe", sa.String(4), nullable=True),
        sa.Column("incident_type", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candle_span", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("scope_type IN ('symbol_tf','feed')", name="ck_incidents_scope"),
        schema="market",
    )
    op.create_index(
        "ix_incidents_series", "data_incidents", ["symbol", "started_at"], schema="market"
    )
    op.create_index(
        "ix_incidents_open",
        "data_incidents",
        ["started_at"],
        schema="market",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("data_incidents", schema="market")
    op.drop_table("candles", schema="market")
    op.drop_table("symbols", schema="market")
    op.execute("DROP SCHEMA IF EXISTS market CASCADE")
