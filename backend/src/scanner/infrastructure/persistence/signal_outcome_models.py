"""Persistence model for T19 `detection.signal_outcomes`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base

_OUTCOMES = "'SUCCESS','FAILED','EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"


class SignalOutcomeRow(Base):
    """Exactly one row per resolved signal. Insert-once, immutable."""

    __tablename__ = "signal_outcomes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["signal_id"],
            ["detection.signals.signal_id"],
            name="fk_signal_outcomes_signal",
        ),
        CheckConstraint(f"outcome IN ({_OUTCOMES})", name="ck_signal_outcomes_outcome"),
        CheckConstraint("mfe_r >= 0 AND mae_r >= 0", name="ck_signal_outcomes_excursions"),
        CheckConstraint("elapsed_candles >= 0", name="ck_signal_outcomes_elapsed"),
        Index("ix_signal_outcomes_stats", "outcome", "resolved_at"),
        {"schema": "detection"},
    )

    signal_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    elapsed_candles: Mapped[int] = mapped_column(Integer(), nullable=False)
    mfe_r: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    mae_r: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    excluded_from_stats: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    resolution_evidence: Mapped[str] = mapped_column(Text(), nullable=False)
