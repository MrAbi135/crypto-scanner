"""Integration: repositories vs real TimescaleDB (Roadmap S1 testing row).

Proves what unit fakes cannot: the COPY→staging→conflict-skip bulk path,
the storage CHECK tripwires, hypertable creation via the real migration,
and incident round-trips. Requires Docker (testcontainers).

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

pytest.importorskip("testcontainers")
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from scanner.application.ports import IncidentRecord
from scanner.domain.common import Symbol, SymbolStatus
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.shared import Timeframe, new_ulid
from tests.support.builders import BASE_TIME, make_series
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration

_IMAGE = "timescale/timescaledb:2.15.2-pg16"


@pytest.fixture(scope="module")
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


async def test_migration_created_hypertable(engine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'candles'"
            )
        )
        assert result.scalar_one() == 1


async def test_bulk_insert_is_idempotent_and_counts(engine) -> None:
    sessions = build_session_factory(engine)
    repo = PgCandleRepository(sessions, FakeClock(BASE_TIME + timedelta(days=10)))
    series = make_series(500)

    assert await repo.bulk_insert(series) == 500
    assert await repo.bulk_insert(series) == 0  # conflict-skip: facts untouched
    assert (
        await repo.bulk_insert(
            series[:250] + make_series(100, start=series[-1].open_time + timedelta(hours=1))
        )
        == 100
    )

    assert await repo.latest_open_time("BTCUSDT", Timeframe.H1) == series[-1].open_time + timedelta(
        hours=100
    )
    fetched = await repo.fetch_series(
        "BTCUSDT", Timeframe.H1, BASE_TIME, BASE_TIME + timedelta(hours=10)
    )
    assert len(fetched) == 10
    assert fetched[0].open == series[0].open  # Decimal survives storage exactly


async def test_check_constraint_rejects_insane_row(engine) -> None:
    """The DDD §18 storage tripwire: even if every code layer failed, the
    database refuses impossible market data."""
    async with engine.connect() as conn:
        with pytest.raises(Exception, match="ck_candles_ohlc"):
            await conn.execute(
                text(
                    "INSERT INTO market.candles VALUES "
                    "('XXXUSDT','H1', now(), 100, 99, 98, 101, 10, 1000, 5, 5, "
                    "'backfill', 0, now())"
                )
            )
        await conn.rollback()


async def test_symbol_upsert_preserves_lifecycle(engine) -> None:
    sessions = build_session_factory(engine)
    repo = PgSymbolRepository(sessions)
    sym = Symbol(
        new_ulid(), "binance", "ETHUSDT", "ETH", "USDT", SymbolStatus.QUARANTINE, BASE_TIME
    )
    await repo.upsert_many([sym])
    # re-sync must NOT reset lifecycle (only venue-DELISTED transitions apply)
    resync = Symbol(
        new_ulid(), "binance", "ETHUSDT", "ETH", "USDT", SymbolStatus.QUARANTINE, BASE_TIME
    )
    await repo.upsert_many([resync])
    stored = await repo.get("ETHUSDT")
    assert stored is not None and stored.id == sym.id  # original row kept


async def test_incident_roundtrip(engine) -> None:
    sessions = build_session_factory(engine)
    repo = PgIncidentRepository(sessions)
    rec = IncidentRecord(
        id=new_ulid(),
        scope_type="symbol_tf",
        incident_type="gap",
        started_at=BASE_TIME,
        symbol="ETHUSDT",
        timeframe=Timeframe.H1,
        candle_span=3,
        resolution="unfillable",
        resolved_at=BASE_TIME,
    )
    await repo.record(rec)
    stored = await repo.list_for_series("ETHUSDT", Timeframe.H1)
    assert len(stored) == 1 and stored[0].candle_span == 3
    assert not await repo.list_open("ETHUSDT")  # resolved ⇒ not open
