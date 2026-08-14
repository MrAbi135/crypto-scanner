"""Detection persistence models (Sprint S4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class AlgoVersionRow(Base):
    """Detection algorithm version registry."""

    __tablename__ = "algo_versions"
    __table_args__ = (
        UniqueConstraint(
            "engine",
            "version",
            name="uq_algo_versions_engine_version",
        ),
        {"schema": "detection"},
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    engine: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class EngineEventRow(Base):
    """Immutable detection event fact."""

    __tablename__ = "engine_events"
    __table_args__ = ({"schema": "detection"},)

    event_key: Mapped[str] = mapped_column(
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

    event_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
    )

    algo_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
