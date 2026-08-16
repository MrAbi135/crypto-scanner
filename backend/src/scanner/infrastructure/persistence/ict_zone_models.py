"""Persistence models for Sprint S6 ICT zones."""

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


class IctZoneRow(Base):
    """Current persisted state of one ICT zone."""

    __tablename__ = "ict_zones"
    __table_args__ = ({"schema": "detection"},)

    zone_id: Mapped[str] = mapped_column(
        String(160),
        primary_key=True,
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

    polarity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    grade: Mapped[str] = mapped_column(
        String(24),
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

    refined_low: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    refined_high: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    created_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    confirmed_index: Mapped[int] = mapped_column(
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

    parent_zone_id: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    dealing_range_id: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    stale_context: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    gap_adjacent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    origin_swept: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )


class IctZoneTransitionRow(Base):
    """Immutable ICT-zone state transition."""

    __tablename__ = "ict_zone_transitions"
    __table_args__ = ({"schema": "detection"},)

    transition_id: Mapped[str] = mapped_column(
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

    from_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    to_state: Mapped[str] = mapped_column(
        String(24),
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
