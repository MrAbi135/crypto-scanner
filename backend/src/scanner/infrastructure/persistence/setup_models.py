"""Persistence model for T16 `detection.setups`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class SetupRow(Base):
    """One gate-passing confluence candidate, published or below floor."""

    __tablename__ = "setups"
    __table_args__ = (
        CheckConstraint("direction IN ('UP','DOWN')", name="ck_setups_direction"),
        CheckConstraint(
            "archetype IS NULL OR archetype IN ('A1','A2','A3','A4','A5')",
            name="ck_setups_archetype",
        ),
        # §8.6 gives every archetype a confidence floor, so a row that cleared
        # a floor without an archetype is a contradiction rather than a gap.
        CheckConstraint(
            "NOT floor_passed OR archetype IS NOT NULL",
            name="ck_setups_floor_needs_archetype",
        ),
        Index("ix_setups_evaluated_at", "evaluated_at"),
        Index("ix_setups_calibration", "archetype", "floor_passed", "evaluated_at"),
        # Redundant against the primary key, which hashes exactly this tuple,
        # and kept for the same reason the §5.9 interaction index is: the
        # invariant should sit where a future change to the hash cannot walk
        # past it.
        Index(
            "uq_setups_identity",
            "symbol",
            "timeframe",
            "direction",
            "evaluated_at",
            "algo_version",
            unique=True,
        ),
        {"schema": "detection"},
    )

    setup_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    archetype: Mapped[str | None] = mapped_column(String(4), nullable=True)
    gate_results: Mapped[str] = mapped_column(Text(), nullable=False)
    factor_scores: Mapped[str] = mapped_column(Text(), nullable=False)
    adjustments: Mapped[str] = mapped_column(Text(), nullable=False)
    base_confidence: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    final_confidence: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    floor_passed: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    algo_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[str] = mapped_column(Text(), nullable=False)
