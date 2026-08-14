"""SQLAlchemy models for the market schema (DDD T1, T3, T8).

Models never cross the repository boundary (TAD §13) — repositories map
them to domain objects at the edge.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SymbolRow(Base):
    """DDD T1 market.symbols."""

    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint(
            "venue",
            "exchange_symbol",
            name="uq_symbols_venue_exchange",
        ),
        CheckConstraint(
            "tier IN ('T1','T2','T3','INELIGIBLE')",
            name="ck_symbols_tier",
        ),
        CheckConstraint(
            "candidate_tier IS NULL OR candidate_tier IN ('T1','T2','T3','INELIGIBLE')",
            name="ck_symbols_candidate_tier",
        ),
        CheckConstraint(
            "consecutive_passes >= 0",
            name="ck_symbols_consecutive_passes_nonnegative",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_symbols_consecutive_failures_nonnegative",
        ),
        {"schema": "market"},
    )

    id: Mapped[str] = mapped_column(
        String(26),
        primary_key=True,
    )
    venue: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    exchange_symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    base_asset: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    quote_asset: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    delisted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    tier: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="INELIGIBLE",
        server_default="INELIGIBLE",
    )
    candidate_tier: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    consecutive_passes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )


class CandleRow(Base):
    """DDD T3 market.candles — hypertable (converted in migration 001).

    Natural composite key (DDD §6); NUMERIC everywhere; CHECK constraints
    mirror SLS §2.15 as the storage-layer tripwire (DDD §18).
    """

    __tablename__ = "candles"
    __table_args__ = (
        CheckConstraint(
            "high >= low",
            name="ck_candles_hl",
        ),
        CheckConstraint(
            "high >= open AND high >= close AND low <= open AND low <= close",
            name="ck_candles_ohlc",
        ),
        CheckConstraint(
            "volume >= 0 AND quote_volume >= 0 AND taker_buy_volume >= 0 "
            "AND taker_buy_volume <= volume AND trade_count >= 0",
            name="ck_candles_volumes",
        ),
        {"schema": "market"},
    )

    symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    timeframe: Mapped[str] = mapped_column(
        String(4),
        primary_key=True,
    )
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    open: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    high: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    low: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    close: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    volume: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    quote_volume: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    taker_buy_volume: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    trade_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class IncidentRow(Base):
    """DDD T8 market.data_incidents — the honesty ledger."""

    __tablename__ = "data_incidents"
    __table_args__ = ({"schema": "market"},)

    id: Mapped[str] = mapped_column(
        String(26),
        primary_key=True,
    )
    scope_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    symbol: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    timeframe: Mapped[str | None] = mapped_column(
        String(4),
        nullable=True,
    )
    incident_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    candle_span: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    resolution: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )


class LiquidityHistoryRow(Base):
    """Daily liquidity observations for S3 universe tiering."""

    __tablename__ = "liquidity_history"
    __table_args__ = (
        CheckConstraint(
            "daily_quote_volume >= 0",
            name="ck_liquidity_history_volume_nonnegative",
        ),
        CheckConstraint(
            "spread_bps >= 0",
            name="ck_liquidity_history_spread_nonnegative",
        ),
        CheckConstraint(
            "depth_2pct >= 0",
            name="ck_liquidity_history_depth_nonnegative",
        ),
        {"schema": "market"},
    )

    exchange_symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    daily_quote_volume: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    spread_bps: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
    depth_2pct: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )
