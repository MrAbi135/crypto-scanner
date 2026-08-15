"""Persistence models for Sprint S5 liquidity detection."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class LiquidityPoolRow(Base):
    """Current persisted state of one liquidity pool."""

    __tablename__ = "liquidity_pools"
    __table_args__ = ({"schema": "detection"},)

    pool_id: Mapped[str] = mapped_column(
        String(160),
        primary_key=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        index=True,
    )

    side: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
    )

    liquidity_class: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    band_low: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    band_high: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    strength: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
    )

    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    member_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )


class LiquidityTransitionRow(Base):
    """Immutable liquidity-pool state transition."""

    __tablename__ = "liquidity_transitions"
    __table_args__ = ({"schema": "detection"},)

    transition_id: Mapped[str] = mapped_column(
        String(160),
        primary_key=True,
    )

    pool_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        index=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        index=True,
    )

    from_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    to_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    candle_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
