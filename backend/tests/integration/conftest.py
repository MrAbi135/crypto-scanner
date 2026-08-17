"""Shared TimescaleDB container for the integration suite.

One migrated container serves every integration module (spinning one per file
costs ~30 s each). Tests must therefore not assume an empty database — use
distinct symbols/ids per module.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("testcontainers")
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from testcontainers.postgres import PostgresContainer

from scanner.infrastructure.persistence.database import build_engine

_IMAGE = "timescale/timescaledb:2.15.2-pg16"


@pytest.fixture(scope="session")
def pg_dsn():
    with PostgresContainer(_IMAGE, username="scanner", password="scanner", dbname="scanner") as pg:
        sync_dsn = pg.get_connection_url()  # psycopg2 form
        async_dsn = sync_dsn.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        os.environ["SCANNER_DB_DSN"] = async_dsn
        cfg = AlembicConfig("alembic.ini")
        alembic_command.upgrade(cfg, "head")
        yield async_dsn


@pytest.fixture()
async def engine(pg_dsn):
    engine = build_engine(pg_dsn, pool_size=2)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def redis_url():
    """One Redis for the suite, on the same reasoning as the database.

    Imported from `testcontainers.community` rather than the deprecated
    `testcontainers.redis` the Postgres fixture above still uses -- new code
    should not adopt a path that already emits a DeprecationWarning.
    """
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)

        yield f"redis://{host}:{port}/0"


@pytest.fixture()
async def redis_client(redis_url):
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)

    # The container is shared, so a test that assumes an empty stream has to
    # make it empty. Cheaper and more honest than per-test containers.
    await client.flushall()

    yield client

    await client.aclose()
