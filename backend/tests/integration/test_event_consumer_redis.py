"""Integration: consumer-group semantics against real Redis (Sprint S4b).

The properties here are Redis's, not ours, and each one is the difference
between an engine that survives a restart and one that silently skips bars.

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import pytest

pytest.importorskip("testcontainers")

from scanner.application.ports.event_consumer import CANDLE_GROUP
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.infrastructure.redis.event_consumer import RedisEventStreamConsumer
from scanner.infrastructure.redis.event_stream import RedisEventStreamPublisher

pytestmark = pytest.mark.integration


def entries(count: int):
    return [{"event_id": f"e{i}", "payload": f'{{"n":{i}}}'} for i in range(count)]


async def test_creating_the_group_twice_is_not_an_error(redis_client) -> None:
    """Every boot after the first hits BUSYGROUP; that is the normal path."""
    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)
    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)


async def test_the_group_can_be_created_before_any_event_exists(redis_client) -> None:
    """The engine may boot before ingest has produced anything.

    Without mkstream the first start fails on a missing key, and the process
    crash-loops until a candle happens to close.
    """
    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group("scanner:stream:nothing-here-yet", CANDLE_GROUP)

    read = await consumer.read(
        "scanner:stream:nothing-here-yet",
        CANDLE_GROUP,
        "engine-1",
        count=10,
        block_ms=10,
    )

    assert read == ()


async def test_entries_arrive_in_order_and_are_delivered_once(redis_client) -> None:
    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)
    await RedisEventStreamPublisher(redis_client).publish(CANDLE_STREAM, entries(4))

    read = await consumer.read(CANDLE_STREAM, CANDLE_GROUP, "engine-1", count=10, block_ms=50)

    assert [entry.fields["event_id"] for entry in read] == ["e0", "e1", "e2", "e3"]

    # Delivered, so a second read of new entries returns nothing.
    assert await consumer.read(CANDLE_STREAM, CANDLE_GROUP, "engine-1", count=10, block_ms=10) == ()


async def test_acked_entries_do_not_come_back_but_unacked_ones_do(redis_client) -> None:
    """The kill -9 case, which is the whole reason for a consumer group.

    A process takes four entries, acks two, and dies. The two it acked are
    finished. The two it did not must be reclaimable by its replacement --
    otherwise they sit in the pending list forever: delivered, so `read` will
    never return them, and unacked, so nothing else will either. The candles
    would exist, the events would exist, and detection would never run for
    those bars, permanently and with nothing to notice.
    """
    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)
    await RedisEventStreamPublisher(redis_client).publish(CANDLE_STREAM, entries(4))

    taken = await consumer.read(CANDLE_STREAM, CANDLE_GROUP, "engine-1", count=10, block_ms=50)

    assert len(taken) == 4

    acked = await consumer.ack(
        CANDLE_STREAM,
        CANDLE_GROUP,
        [taken[0].entry_id, taken[1].entry_id],
    )

    assert acked == 2

    # "engine-1" is now gone. Its replacement claims whatever it left pending.
    reclaimed = await consumer.claim_stale(
        CANDLE_STREAM,
        CANDLE_GROUP,
        "engine-2",
        min_idle_ms=0,
        count=10,
    )

    assert [entry.fields["event_id"] for entry in reclaimed] == ["e2", "e3"]

    assert (
        await consumer.ack(CANDLE_STREAM, CANDLE_GROUP, [entry.entry_id for entry in reclaimed])
        == 2
    )

    # Fully drained: nothing new, nothing pending.
    assert (
        await consumer.claim_stale(CANDLE_STREAM, CANDLE_GROUP, "engine-2", min_idle_ms=0, count=10)
        == ()
    )


async def test_claiming_ignores_entries_still_being_worked_on(redis_client) -> None:
    """min_idle_ms is what stops a healthy consumer being robbed mid-batch."""
    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)
    await RedisEventStreamPublisher(redis_client).publish(CANDLE_STREAM, entries(2))

    await consumer.read(CANDLE_STREAM, CANDLE_GROUP, "engine-1", count=10, block_ms=50)

    # Idle for milliseconds, threshold of a minute: not stale.
    assert (
        await consumer.claim_stale(
            CANDLE_STREAM, CANDLE_GROUP, "engine-2", min_idle_ms=60_000, count=10
        )
        == ()
    )
