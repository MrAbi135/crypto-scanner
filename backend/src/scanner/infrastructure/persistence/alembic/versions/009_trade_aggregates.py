"""T4 market.trade_aggregates_1m (DDD §T4, SLS §2.2).

One row per (symbol, minute): the taker split and the size distribution that
§6.5's institutional-volume signature and §6.6's trade-size uniformity test
read. SLS §2 is explicit that raw prints are not retained -- "aggTrades
preserve taker side and size distribution, which is all current doctrine
consumes" -- so this table is the record, not a cache of one.

**`stddev_trade_size` is not in DDD T4's field list, and is here anyway.**
§6.6's third test is "coefficient of variation of trade sizes < 0.2", and a
coefficient of variation is stddev over mean. T4 as listed carries mean, p90
and max, from which no dispersion can be recovered -- so the test could be
specified and never computed. Storing the population stddev of the minute's
prints is the smallest addition that makes it computable, and it composes
across minutes exactly when the count and mean are stored beside it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009_trade_aggregates"
down_revision = "008_outbox_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_aggregates_1m",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("minute_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taker_buy_volume", sa.Numeric, nullable=False),
        sa.Column("taker_sell_volume", sa.Numeric, nullable=False),
        sa.Column("trade_count", sa.BigInteger, nullable=False),
        sa.Column("mean_trade_size", sa.Numeric, nullable=False),
        sa.Column("stddev_trade_size", sa.Numeric, nullable=False),
        sa.Column("p90_trade_size", sa.Numeric, nullable=False),
        sa.Column("max_trade_size", sa.Numeric, nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "minute_ts", name="pk_trade_aggregates_1m"),
        sa.CheckConstraint(
            "taker_buy_volume >= 0 AND taker_sell_volume >= 0 AND trade_count >= 0",
            name="ck_trade_aggregates_nonnegative",
        ),
        # A bucket exists because prints arrived, so a zero count would be a
        # row about nothing -- and it would divide by zero in every mean.
        sa.CheckConstraint("trade_count > 0", name="ck_trade_aggregates_has_prints"),
        sa.CheckConstraint(
            # `p90 >= mean` is deliberately not asserted: it holds for the
            # right-skewed distributions real tape produces, and fails for a
            # uniform one, so it would reject valid data to state a tendency.
            "mean_trade_size > 0 AND stddev_trade_size >= 0 "
            "AND p90_trade_size > 0 AND max_trade_size >= p90_trade_size",
            name="ck_trade_aggregates_sizes",
        ),
        schema="market",
    )

    # 7-day chunks, matching T1 and DDD §13's table for T4.
    op.execute(
        "SELECT create_hypertable('market.trade_aggregates_1m', 'minute_ts', "
        "chunk_time_interval => INTERVAL '7 days', migrate_data => TRUE)"
    )

    op.execute(
        "ALTER TABLE market.trade_aggregates_1m SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol', "
        "timescaledb.compress_orderby = 'minute_ts')"
    )

    op.execute("SELECT add_compression_policy('market.trade_aggregates_1m', INTERVAL '7 days')")

    # DDD T4: "90 days full -> hourly rollup retained 2 years". The rollup is
    # not built, so retention is left off rather than dropping data that has
    # nowhere to roll into.


def downgrade() -> None:
    op.drop_table("trade_aggregates_1m", schema="market")
