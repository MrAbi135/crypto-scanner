"""Integration: the whole S4b chain, end to end (Sprint S4b).

candle → outbox → relay → stream → consumer → detection dispatch.

Every link has its own test elsewhere. This one exists because a chain of
individually-correct links is not the same claim as a working chain, and the
question S4b answers is whether a candle closing causes detection to happen
without anyone typing anything.

The only substitution is the detection runner: it records what it was asked to
detect instead of detecting. That keeps the test about *delivery* -- a real
pipeline would need 300 warm candles per context and would be asserting the
detectors, which the golden suite already does.

Run: pytest -m integration tests/integration
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.detection.candle_close_consumer import CandleCloseConsumer
from scanner.application.marketdata.outbox_relay import OutboxRelayService
from scanner.application.ports.event_consumer import CANDLE_GROUP
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.domain.common import Candle, CandleSource
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.outbox_repository import PgOutboxRepository
from scanner.infrastructure.persistence.repositories import PgCandleRepository
from scanner.infrastructure.redis.event_consumer import RedisEventStreamConsumer
from scanner.infrastructure.redis.event_stream import RedisEventStreamPublisher
from scanner.shared import Timeframe
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration

BASE = datetime(2026, 8, 17, tzinfo=UTC)


class SpyRunner:
    def __init__(self) -> None:
        self.passes: list[tuple[str, str, datetime]] = []

    async def run(self, symbol: str, timeframe: Timeframe, open_time: datetime) -> None:
        self.passes.append((symbol, timeframe.value, open_time))


def candle(symbol: str, timeframe: Timeframe, index: int) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=BASE + timeframe.duration * index,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        taker_buy_volume=Decimal("6"),
        trade_count=50,
        source=CandleSource.STREAM,
    )


async def test_a_closing_candle_causes_a_detection_pass_with_nobody_watching(
    engine,
    redis_client,
) -> None:
    symbol = "CHAINA"

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM ops.outbox_events"))

    sessions = build_session_factory(engine)
    clock = FakeClock()

    candles = PgCandleRepository(sessions, clock)

    # Two H1s inserted newest-first, plus an H4. The disorder is deliberate:
    # if the chain preserved arrival order rather than imposing its own, the
    # detection passes below would come out in this order instead.
    written = await candles.bulk_insert(
        [
            candle(symbol, Timeframe.H1, 1),
            candle(symbol, Timeframe.H1, 0),
        ],
        emit_outbox=True,
    )

    written += await candles.bulk_insert(
        [candle(symbol, Timeframe.H4, 0)],
        emit_outbox=True,
    )

    assert written == 3

    consumer = RedisEventStreamConsumer(redis_client)

    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)

    relay = OutboxRelayService(
        PgOutboxRepository(sessions),
        RedisEventStreamPublisher(redis_client),
        clock,
    )

    assert (await relay.sweep()).published == 3

    async with engine.connect() as conn:
        unrelayed = await conn.execute(
            text("SELECT count(*) FROM ops.outbox_events WHERE relayed_at IS NULL")
        )

        assert unrelayed.scalar_one() == 0

    entries = await consumer.read(
        CANDLE_STREAM,
        CANDLE_GROUP,
        "chain-engine",
        count=50,
        block_ms=500,
    )

    assert len(entries) == 3

    spy = SpyRunner()

    report, acked = await CandleCloseConsumer(spy).consume(entries)

    await consumer.ack(CANDLE_STREAM, CANDLE_GROUP, list(acked))

    assert report.processed == 3
    assert report.failed == 0

    # HTF before LTF, and chronological within the H1 context -- both restored
    # from an insert order that had neither.
    assert [timeframe for _, timeframe, _ in spy.passes] == ["H4", "H1", "H1"]
    assert spy.passes[1][2] < spy.passes[2][2]

    # Acked work does not come back.
    assert (
        await consumer.claim_stale(
            CANDLE_STREAM,
            CANDLE_GROUP,
            "chain-engine",
            min_idle_ms=0,
            count=50,
        )
        == ()
    )
