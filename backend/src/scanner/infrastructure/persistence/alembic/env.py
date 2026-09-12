"""Alembic environment (S0.2): async engine.

DSN from `SCANNER_MIGRATION_DB_DSN` when set, else `SCANNER_DB_DSN`.

Two variables because DDD's grant layer needs two roles. The application must
connect as a role that cannot UPDATE, DELETE or TRUNCATE the sealed signal
tables -- and cannot drop their triggers, which only an owner can -- while
migrations need DDL and so must run as the owner. If both read one variable,
either the application runs as the owner (the layer is absent, which the engine
logs at every boot as `immutability_grant_layer_absent`) or migrations run as
the restricted role and fail on their first `CREATE`.

The migration DSN is meant to be supplied only to the migration run, never
loaded into the long-running containers: a publishing process that can read
the owner's credential is a publishing process that can drop the guard. See
`docs/runbooks/deploy-p1b.md#least-privilege-role`.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from scanner.infrastructure.persistence.models import Base

target_metadata = Base.metadata


def _dsn() -> str:
    dsn = os.environ.get("SCANNER_MIGRATION_DB_DSN") or os.environ.get("SCANNER_DB_DSN")
    if not dsn:
        raise RuntimeError(
            "SCANNER_MIGRATION_DB_DSN or SCANNER_DB_DSN is required (use scripts/with-env.sh)"
        )
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
