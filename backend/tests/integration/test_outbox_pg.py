"""Integration: the T39 transactional outbox vs real Postgres (Roadmap S4b).

These assertions cannot be made against a fake. The guarantee under test is
that the event and the candle share a transaction, and "shares a transaction"
is a property of the database, not of the code that calls it.

The `pg_dsn` / `engine` fixtures live in `tests/integration/conftest.py`. The
container is shared across modules, so every test here uses its own symbol.

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.ports.outbox import CANDLE_CLOSED_EVENT
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.repositories import PgCandleRepository
from scanner.shared import Timeframe
from tests.support.builders import BASE_TIME, make_candle
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration


def series(symbol: str, count: int):
    """A per-symbol series -- `make_series` fixes the symbol at BTCUSDT.

    The integration container is shared across modules, so every test needs
    its own symbol or they collide through the database.
    """
    return [
        make_candle(
            symbol=symbol,
            timeframe=Timeframe.H1,
            open_time=BASE_TIME + Timeframe.H1.duration * i,
        )
        for i in range(count)
    ]


async def _outbox_rows(engine, symbol: str):
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT id, aggregate_type, aggregate_id, event_type, payload, "
                "relayed_at, relay_attempts "
                "FROM ops.outbox_events "
                "WHERE aggregate_id LIKE :prefix "
                "ORDER BY id"
            ),
            {"prefix": f"{symbol}:%"},
        )

        return result.mappings().all()


async def test_a_live_close_writes_exactly_one_event(engine) -> None:
    symbol = "OUTBOXA"

    repo = PgCandleRepository(build_session_factory(engine), FakeClock())

    candles = series(symbol, 3)

    inserted = await repo.bulk_insert(candles, emit_outbox=True)

    assert inserted == 3

    rows = await _outbox_rows(engine, symbol)

    assert len(rows) == 3

    first = rows[0]

    assert first["event_type"] == CANDLE_CLOSED_EVENT
    assert first["aggregate_type"] == "candle"
    assert first["relayed_at"] is None
    assert first["relay_attempts"] == 0

    # The payload must name the candle precisely enough for a consumer to fetch
    # it. A consumer that has to guess the timeframe is a consumer that will.
    payload = json.loads(first["payload"])["payload"]

    assert payload["symbol"] == symbol
    assert payload["timeframe"] == Timeframe.H1.value
    assert payload["open_time"] == candles[0].open_time.isoformat()


async def test_backfill_inserts_the_same_candles_and_announces_nothing(engine) -> None:
    """The flag is the whole difference between a backfill and a live close."""
    symbol = "OUTBOXB"

    repo = PgCandleRepository(build_session_factory(engine), FakeClock())

    inserted = await repo.bulk_insert(
        series(symbol, 5),
    )

    assert inserted == 5
    assert await _outbox_rows(engine, symbol) == []


async def test_a_duplicate_frame_does_not_re_announce_the_close(engine) -> None:
    """ON CONFLICT DO NOTHING must suppress the event, not just the row.

    Binance re-sends frames. If the outbox counted candles offered rather than
    candles accepted, every duplicate would replay a close the engine already
    processed -- harmless downstream thanks to persistence uniqueness, but it
    would make the stream a liar about what happened in the market.
    """
    symbol = "OUTBOXC"

    repo = PgCandleRepository(build_session_factory(engine), FakeClock())

    candles = series(symbol, 4)

    assert await repo.bulk_insert(candles, emit_outbox=True) == 4
    assert len(await _outbox_rows(engine, symbol)) == 4

    # The exact same frames again.
    assert await repo.bulk_insert(candles, emit_outbox=True) == 0
    assert len(await _outbox_rows(engine, symbol)) == 4

    # A partial overlap: three known, two new.
    extended = series(symbol, 6)

    assert await repo.bulk_insert(extended, emit_outbox=True) == 2

    rows = await _outbox_rows(engine, symbol)

    assert len(rows) == 6

    announced = {json.loads(row["payload"])["payload"]["open_time"] for row in rows}

    assert announced == {candle.open_time.isoformat() for candle in extended}


async def test_the_relay_queue_index_is_partial_on_unrelayed(engine) -> None:
    """The index exists and is partial -- the relayed set must not be indexed.

    Asserted because it is invisible at runtime: a full index would work, stay
    correct, and quietly grow forever alongside the table it is not read from.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'ops' AND indexname = 'ix_outbox_events_unrelayed'"
            )
        )

        indexdef = result.scalar_one()

    assert "relayed_at IS NULL" in indexdef


async def test_a_failed_event_write_takes_the_candle_down_with_it(
    engine,
    monkeypatch,
) -> None:
    """The guarantee itself: candle and event commit together or not at all.

    Everything above tests what the outbox writes. This tests why it is an
    outbox rather than a second write after the first: if the event insert
    fails, the candle must not survive. Otherwise the engine's view of the
    market silently diverges from the database's, and nothing ever notices --
    the candle is there, no close was ever announced, and detection simply
    skips a bar forever.

    The failure is injected rather than provoked, because every natural way to
    break the event insert also breaks the candle insert, which would pass the
    test for the wrong reason.
    """
    symbol = "OUTBOXD"

    repo = PgCandleRepository(build_session_factory(engine), FakeClock())

    async def explode(*args, **kwargs):
        raise RuntimeError("relay table unavailable")

    monkeypatch.setattr(repo, "_append_candle_events", explode)

    with pytest.raises(RuntimeError, match="relay table unavailable"):
        await repo.bulk_insert(series(symbol, 3), emit_outbox=True)

    async with engine.connect() as conn:
        surviving = await conn.execute(
            text("SELECT count(*) FROM market.candles WHERE symbol = :s"),
            {"s": symbol},
        )

        assert surviving.scalar_one() == 0

    assert await _outbox_rows(engine, symbol) == []
