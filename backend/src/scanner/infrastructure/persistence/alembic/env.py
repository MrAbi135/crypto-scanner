"""Alembic environment (S0.2): async engine, DSN from SCANNER_DB_DSN."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from scanner.infrastructure.persistence.models import Base

target_metadata = Base.metadata


def _dsn() -> str:
    dsn = os.environ.get("SCANNER_DB_DSN")
    if not dsn:
        raise RuntimeError("SCANNER_DB_DSN is required (use scripts/with-env.sh)")
    return dsn


def run_migrations_offline() -> None:
    context.configure(url=_dsn(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_dsn())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
