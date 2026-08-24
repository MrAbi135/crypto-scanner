"""Persistence model for T18 `detection.signal_transitions`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base

_STATES = (
    "'DETECTED','PUBLISHED','SUPPRESSED','ACTIVE','SUCCESS','FAILED',"
    "'EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"
)


class SignalTransitionRow(Base):
    """One §12 transition. Append-only; a signal's state is the latest row."""

    __tablename__ = "signal_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["signal_id"],
            ["detection.signals.signal_id"],
            name="fk_signal_transitions_signal",
        ),
        CheckConstraint(f"from_state IN ({_STATES})", name="ck_signal_transitions_from"),
        CheckConstraint(f"to_state IN ({_STATES})", name="ck_signal_transitions_to"),
        UniqueConstraint(
            "signal_id",
            "at_candle_open_time",
            name="uq_signal_transitions_signal_candle",
        ),
        Index("ix_signal_transitions_signal_time", "signal_id", "at_candle_open_time"),
        {"schema": "detection"},
    )

    transition_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(160), nullable=False)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    at_candle_open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stress_test: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    trigger_evidence: Mapped[str] = mapped_column(Text(), nullable=False)
