"""Persistence model for immutable ICT zone interactions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class IctZoneInteractionRow(Base):
    """Immutable SLS §5.9 interaction fact."""

    __tablename__ = "ict_zone_interactions"
    __table_args__ = ({"schema": "detection"},)

    interaction_id: Mapped[str] = mapped_column(
        String(160),
        primary_key=True,
    )

    zone_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )

    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
    )

    zone_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    candle_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    penetration_depth: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    close_price: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    rejection_wick: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    close_through: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
