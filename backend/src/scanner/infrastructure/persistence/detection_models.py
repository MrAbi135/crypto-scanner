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
        # `param_set_version` joins the key because a parameter change keeps
        # the algo version and increments the set -- see migration 013 for why
        # `engine` stays in it.
        UniqueConstraint(
            "engine",
            "version",
            "param_set_version",
            name="uq_algo_versions_engine_version_param_set",
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

    param_set_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    param_payload: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    # Null means the row predates verification, not that the checksum is
    # empty. Boot fills it in; it never compares against a null.
    checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    sls_reference: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    deployed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
