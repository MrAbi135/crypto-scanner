"""SQLAlchemy models for T20 `identity.tenants` and T21 `identity.users`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scanner.infrastructure.persistence.models import Base


class TenantRow(Base):
    __tablename__ = "tenants"
    __table_args__ = ({"schema": "identity"},)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": "identity"},)

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    # Mapped so the model matches migration 019, which matches DDD T21.
    # Nothing in S10-minimal reads them for a decision -- see
    # `UNUSED_IDENTITY_COLUMNS`.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text(), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
