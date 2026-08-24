"""Persistence model for T17 `detection.signals` — the crown jewel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class SignalRow(Base):
    """One published signal. Insert-once; no UPDATE surface exists."""

    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("direction IN ('UP','DOWN')", name="ck_signals_direction"),
        CheckConstraint(
            "archetype IN ('A1','A2','A3','A4','A5')",
            name="ck_signals_archetype",
        ),
        CheckConstraint("grade IN ('S','A','B')", name="ck_signals_grade"),
        CheckConstraint("ttl_candles > 0", name="ck_signals_ttl"),
        CheckConstraint("entry_proximal <> entry_distal", name="ck_signals_entry_band"),
        Index("ix_signals_published_at", "published_at"),
        Index("ix_signals_symbol_published", "symbol", "published_at"),
        Index("ix_signals_stats", "archetype", "grade", "published_at"),
        # Deliberately not unique -- see migration 014. "Active" is T18's to
        # know, and T17 has no column a partial index could predicate on.
        Index("ix_signals_dedup_key", "dedup_key", "published_at"),
        {"schema": "detection"},
    )

    signal_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    setup_id: Mapped[str] = mapped_column(String(160), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    archetype: Mapped[str] = mapped_column(String(4), nullable=False)
    grade: Mapped[str] = mapped_column(String(2), nullable=False)
    final_confidence: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    entry_proximal: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    entry_distal: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    invalidation_level: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    target_bands: Mapped[str] = mapped_column(Text(), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ttl_candles: Mapped[int] = mapped_column(Integer(), nullable=False)
    algo_version: Mapped[str] = mapped_column(String(64), nullable=False)
    param_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
