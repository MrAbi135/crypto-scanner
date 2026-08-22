"""Integration: repositories vs real TimescaleDB (Roadmap S1 testing row).

Proves what unit fakes cannot: the COPY→staging→conflict-skip bulk path,
the storage CHECK tripwires, hypertable creation via the real migration,
and incident round-trips. Requires Docker (testcontainers).

The `pg_dsn` / `engine` fixtures live in `tests/integration/conftest.py`.

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.ports import IncidentRecord
from scanner.application.ports.repositories import UniverseStateRecord
from scanner.domain.common import Symbol, SymbolStatus
from scanner.domain.common.universe import UniverseTier
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.shared import Timeframe, new_ulid
from tests.support.builders import BASE_TIME, make_series
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration


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


async def _seed(repo, exchange_symbol: str, status: SymbolStatus) -> None:
    # `Symbol` asserts exchange_symbol == base + quote.
    base = exchange_symbol.removesuffix("USDT")

    await repo.upsert_many(
        [Symbol(new_ulid(), "binance", exchange_symbol, base, "USDT", status, BASE_TIME)]
    )


def _state(exchange_symbol: str, tier: UniverseTier) -> UniverseStateRecord:
    return UniverseStateRecord(
        exchange_symbol=exchange_symbol,
        tier=tier,
        candidate_tier=None,
        consecutive_passes=0,
        consecutive_failures=0,
    )


async def test_an_eligible_tier_promotes_a_quarantined_symbol(engine) -> None:
    """§1.4's tier decides membership of the scanned universe.

    Nothing wrote a status after `symbol_sync` set QUARANTINE at first sight,
    so `SymbolStatus.ACTIVE` existed only inside `list_active`'s own filter and
    no symbol could ever satisfy it.
    """
    repo = PgSymbolRepository(build_session_factory(engine))

    await _seed(repo, "PROMOTEUSDT", SymbolStatus.QUARANTINE)
    await repo.save_universe_state(_state("PROMOTEUSDT", UniverseTier.T2))

    stored = await repo.get("PROMOTEUSDT")

    assert stored is not None
    assert stored.status is SymbolStatus.ACTIVE


async def test_an_ineligible_tier_returns_a_symbol_to_quarantine(engine) -> None:
    repo = PgSymbolRepository(build_session_factory(engine))

    await _seed(repo, "DEMOTEUSDT", SymbolStatus.QUARANTINE)

    await repo.save_universe_state(_state("DEMOTEUSDT", UniverseTier.T1))

    # Asserted here as well as at the end: the symbol starts in QUARANTINE, so
    # without this line an implementation that writes no status at all would
    # satisfy the final assertion without ever having demoted anything.
    promoted = await repo.get("DEMOTEUSDT")

    assert promoted is not None
    assert promoted.status is SymbolStatus.ACTIVE

    await repo.save_universe_state(_state("DEMOTEUSDT", UniverseTier.INELIGIBLE))

    stored = await repo.get("DEMOTEUSDT")

    assert stored is not None
    assert stored.status is SymbolStatus.QUARANTINE


async def test_a_delisted_symbol_is_not_revived_by_a_good_tier(engine) -> None:
    """§1.5 makes delisting the exchange's fact, not the liquidity job's.

    A guard rather than a regression test: it passes on the old code too,
    where nothing wrote a status at all. It is here to stop the new write
    from reaching further than §1.4 allows.
    """
    repo = PgSymbolRepository(build_session_factory(engine))

    await _seed(repo, "GONEUSDT", SymbolStatus.DELISTED)
    await repo.save_universe_state(_state("GONEUSDT", UniverseTier.T1))

    stored = await repo.get("GONEUSDT")

    assert stored is not None
    assert stored.status is SymbolStatus.DELISTED


async def test_list_observable_includes_quarantine_but_not_delisted(engine) -> None:
    """The evaluation loop has to see the symbols it might promote.

    Iterating `list_active()` meant it woke every midnight and found nothing:
    on the VM, 484 QUARANTINE, 249 DELISTED and zero ACTIVE.
    """
    repo = PgSymbolRepository(build_session_factory(engine))

    await _seed(repo, "OBSQUARUSDT", SymbolStatus.QUARANTINE)
    await _seed(repo, "OBSGONEUSDT", SymbolStatus.DELISTED)
    await _seed(repo, "OBSLIVEUSDT", SymbolStatus.QUARANTINE)
    await repo.save_universe_state(_state("OBSLIVEUSDT", UniverseTier.T3))

    observable = {s.exchange_symbol for s in await repo.list_observable()}

    assert "OBSQUARUSDT" in observable
    assert "OBSLIVEUSDT" in observable
    assert "OBSGONEUSDT" not in observable


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
