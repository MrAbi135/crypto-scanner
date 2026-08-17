"""Integration: the relay drains Postgres onto a real Redis Stream (S4b).

Three things here cannot be shown with fakes, and each is load-bearing:

* ``FOR UPDATE SKIP LOCKED`` -- lock semantics belong to the database.
* ``xadd`` field encoding and stream ordering -- Redis owns both.
* Idempotent ``mark_relayed`` -- the ``relayed_at IS NULL`` guard is SQL.

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.marketdata.outbox_relay import OutboxRelayService
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.outbox_repository import PgOutboxRepository
from scanner.infrastructure.persistence.repositories import PgCandleRepository
from scanner.infrastructure.redis.event_stream import RedisEventStreamPublisher
from scanner.shared import Timeframe
from tests.support.builders import BASE_TIME, make_candle
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration


def series(symbol: str, count: int):
    return [
        make_candle(
            symbol=symbol,
            timeframe=Timeframe.H1,
            open_time=BASE_TIME + Timeframe.H1.duration * i,
        )
        for i in range(count)
    ]


async def _clear_outbox(engine) -> None:
    """The container is shared; earlier modules leave rows behind."""
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM ops.outbox_events"))


def build_relay(engine, redis_client, **kwargs):
    sessions = build_session_factory(engine)

    return OutboxRelayService(
        PgOutboxRepository(sessions),
        RedisEventStreamPublisher(redis_client),
        FakeClock(),
        **kwargs,
    )


async def test_committed_closes_reach_the_stream_in_commit_order(
    engine,
    redis_client,
) -> None:
    await _clear_outbox(engine)

    symbol = "RELAYA"

    candles = PgCandleRepository(build_session_factory(engine), FakeClock())

    await candles.bulk_insert(series(symbol, 5), emit_outbox=True)

    report = await build_relay(engine, redis_client).sweep()

    assert report.claimed == 5
    assert report.published == 5
    assert report.marked == 5

    entries = await redis_client.xrange(CANDLE_STREAM)

    assert len(entries) == 5

    open_times = [json.loads(fields["payload"])["payload"]["open_time"] for _, fields in entries]

    assert open_times == [candle.open_time.isoformat() for candle in series(symbol, 5)]


async def test_a_drained_outbox_is_not_relayed_twice(
    engine,
    redis_client,
) -> None:
    """The partial index is the queue, so a marked row must leave it."""
    await _clear_outbox(engine)

    symbol = "RELAYB"

    candles = PgCandleRepository(build_session_factory(engine), FakeClock())

    await candles.bulk_insert(series(symbol, 3), emit_outbox=True)

    relay = build_relay(engine, redis_client)

    assert (await relay.sweep()).published == 3

    second = await relay.sweep()

    assert second.claimed == 0
    assert second.published == 0

    assert len(await redis_client.xrange(CANDLE_STREAM)) == 3


async def test_a_redelivered_batch_does_not_rewrite_relayed_at(
    engine,
    redis_client,
) -> None:
    """The crash case: publish succeeded, mark_relayed never ran.

    The next sweep re-publishes -- at-least-once, absorbed downstream by
    persistence uniqueness. What must not happen is the second mark moving the
    timestamp, because relayed_at is the audit answer to "when did this leave"
    and the honest answer is the first time, not the retry.
    """
    await _clear_outbox(engine)

    symbol = "RELAYC"

    sessions = build_session_factory(engine)

    candles = PgCandleRepository(sessions, FakeClock())

    await candles.bulk_insert(series(symbol, 2), emit_outbox=True)

    outbox = PgOutboxRepository(sessions)

    claimed = await outbox.claim_unrelayed(10)
    ids = [record.id for record in claimed]

    first = FakeClock().now()

    assert await outbox.mark_relayed(ids, relayed_at=first) == 2

    later = first.replace(year=first.year + 1)

    # Second attempt at the same ids: nothing left to mark.
    assert await outbox.mark_relayed(ids, relayed_at=later) == 0

    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT DISTINCT relayed_at FROM ops.outbox_events WHERE relayed_at IS NOT NULL")
        )

        assert [row[0] for row in rows] == [first]


async def test_a_second_relay_claims_nothing_the_first_holds(
    engine,
    redis_client,
) -> None:
    """SKIP LOCKED: safety against an accidental second instance.

    Not a scaling design -- two relays interleave and the stream stops
    reflecting commit order. This asserts only that they never publish the
    same row twice, which is the property the lock is actually there for.
    """
    await _clear_outbox(engine)

    symbol = "RELAYD"

    sessions = build_session_factory(engine)

    await PgCandleRepository(sessions, FakeClock()).bulk_insert(
        series(symbol, 4),
        emit_outbox=True,
    )

    # Hold the rows in an open transaction, as a mid-sweep relay would.
    async with sessions() as holder:
        held = await holder.execute(
            text(
                "SELECT id FROM ops.outbox_events WHERE relayed_at IS NULL "
                "ORDER BY id FOR UPDATE SKIP LOCKED"
            )
        )

        assert len(held.fetchall()) == 4

        contender = await PgOutboxRepository(sessions).claim_unrelayed(10)

        assert contender == ()

        await holder.rollback()

    # Released, so the next sweep sees all four again -- nothing was consumed.
    assert (await build_relay(engine, redis_client).sweep()).published == 4
