"""Outbox relay sweep semantics (Sprint S4b).

The relay's whole design is the order of three steps -- claim, publish, mark --
and the interesting tests are the ones that interrupt it between them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scanner.application.marketdata.outbox_relay import OutboxRelayService
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.application.ports.outbox import (
    CANDLE_CLOSED_EVENT,
    OutboxRecord,
)

RELAYED_AT = datetime(2026, 8, 17, 12, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return RELAYED_AT


def record(index: int, *, event_type: str = CANDLE_CLOSED_EVENT) -> OutboxRecord:
    return OutboxRecord(
        id=f"01J{index:05d}",
        aggregate_type="candle",
        aggregate_id=f"BTCUSDT:H1:{index}",
        event_type=event_type,
        payload=f'{{"n":{index}}}',
        created_at=RELAYED_AT,
        relay_attempts=0,
    )


class FakeOutbox:
    def __init__(self, queued: list[OutboxRecord]) -> None:
        self.queued = queued
        self.marked: list[str] = []
        self.failed: list[str] = []

    async def claim_unrelayed(self, limit: int) -> tuple[OutboxRecord, ...]:
        return tuple(self.queued[:limit])

    async def mark_relayed(self, ids, *, relayed_at) -> int:
        assert relayed_at == RELAYED_AT
        self.marked.extend(ids)
        return len(ids)

    async def record_relay_failure(self, ids) -> int:
        self.failed.extend(ids)
        return len(ids)


class FakePublisher:
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode
        self.published: list[tuple[str, tuple[dict[str, str], ...]]] = []

    async def publish(self, stream: str, entries) -> int:
        if self.explode:
            raise ConnectionError("redis unreachable")

        self.published.append((stream, tuple(dict(e) for e in entries)))
        return len(entries)


def build(queued: list[OutboxRecord], *, explode: bool = False, batch_size: int = 200):
    outbox = FakeOutbox(queued)
    publisher = FakePublisher(explode=explode)

    service = OutboxRelayService(
        outbox,
        publisher,
        FakeClock(),
        batch_size=batch_size,
    )

    return service, outbox, publisher


@pytest.mark.asyncio
async def test_an_empty_queue_is_not_a_publish() -> None:
    service, outbox, publisher = build([])

    report = await service.sweep()

    assert report.claimed == 0
    assert publisher.published == []
    assert outbox.marked == []


@pytest.mark.asyncio
async def test_a_sweep_publishes_in_claim_order_and_marks_what_it_sent() -> None:
    service, outbox, publisher = build([record(i) for i in range(3)])

    report = await service.sweep()

    assert report.claimed == 3
    assert report.published == 3
    assert report.marked == 3

    stream, entries = publisher.published[0]

    assert stream == CANDLE_STREAM
    assert [entry["aggregate_id"] for entry in entries] == [
        "BTCUSDT:H1:0",
        "BTCUSDT:H1:1",
        "BTCUSDT:H1:2",
    ]

    # The consumer needs the payload verbatim -- the relay is a pipe, not a
    # translator. Anything it reshapes here is a second place doctrine lives.
    assert entries[0]["payload"] == '{"n":0}'
    assert entries[0]["event_id"] == outbox.marked[0]


@pytest.mark.asyncio
async def test_a_stream_failure_marks_nothing_and_counts_the_attempt() -> None:
    """The crash that must not lose anything.

    If publish fails and the relay marked the rows anyway, those closes would
    be gone: committed candles that no consumer was ever told about, with the
    outbox reporting itself fully drained. Nothing downstream could detect it.
    """
    service, outbox, _ = build([record(i) for i in range(2)], explode=True)

    with pytest.raises(ConnectionError):
        await service.sweep()

    assert outbox.marked == []
    assert outbox.failed == ["01J00000", "01J00001"]


@pytest.mark.asyncio
async def test_the_batch_size_bounds_a_sweep() -> None:
    service, _, publisher = build([record(i) for i in range(10)], batch_size=4)

    report = await service.sweep()

    assert report.claimed == 4
    assert len(publisher.published[0][1]) == 4


@pytest.mark.asyncio
async def test_an_event_with_no_stream_is_refused_rather_than_skipped() -> None:
    """A silently skipped event would sit unrelayed forever, looking healthy.

    The partial index makes it invisible in monitoring (it is genuinely
    unrelayed, so it is genuinely in the queue), and the relay would keep
    claiming and ignoring it on every sweep. Better to fail at the point the
    assumption breaks.
    """
    service, outbox, publisher = build(
        [
            record(0),
            record(1, event_type="signal.published"),
        ]
    )

    with pytest.raises(ValueError, match=r"signal\.published"):
        await service.sweep()

    assert publisher.published == []
    assert outbox.marked == []


def test_a_non_positive_batch_size_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        OutboxRelayService(
            FakeOutbox([]),
            FakePublisher(),
            FakeClock(),
            batch_size=0,
        )
