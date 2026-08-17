"""engine process composition root (S0.2 §9, Sprint S4b).

Until S4b this file was twenty-one lines: settings, logging, a health server,
and a docstring promising that "the detection pipeline arrives from Sprint S4+".
S4, S5 and S6 shipped seventeen thousand lines of doctrine past it, all of it
reachable only by typing `engine run --symbol … --start … --end …` by hand.

It now consumes candle closes and runs detection on its own.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from starlette.applications import Starlette

from scanner.application.detection.candle_close_consumer import CandleCloseConsumer
from scanner.application.detection.trailing_window import TrailingWindowRunner
from scanner.application.ports.event_consumer import CANDLE_GROUP
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.config import get_settings
from scanner.config.processes import EngineSettings
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.redis.event_consumer import RedisEventStreamConsumer
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.detection import build_detection_pipeline
from scanner.runtime.wiring.health import build_health_app, run_asgi

log = structlog.get_logger(__name__)

_READ_BATCH = 32
_BLOCK_MS = 5_000

# Long enough that a consumer merely working through a slow batch is not robbed
# mid-pass, short enough that a killed process's entries are picked up within a
# minute rather than at the next restart.
_CLAIM_IDLE_MS = 60_000


def _consumer_name() -> str:
    """Stable per-container identity.

    The pending list is keyed by consumer name, so a restarted process must
    present the same one to reclaim its own unfinished work through the normal
    path. A random name each boot would leave the old consumer's entries to be
    swept only by the idle-claim, which is the slower fallback.
    """
    return f"engine-{os.getenv('HOSTNAME', 'local')}"


async def _consume_forever(
    consumer: RedisEventStreamConsumer,
    closes: CandleCloseConsumer,
    name: str,
) -> None:
    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)

    log.info("engine_consumer_started", consumer=name, stream=CANDLE_STREAM)

    while True:
        # Stale entries first. A batch abandoned by a killed process is older
        # than anything unread, and the market does not care that we restarted.
        entries = await consumer.claim_stale(
            CANDLE_STREAM,
            CANDLE_GROUP,
            name,
            min_idle_ms=_CLAIM_IDLE_MS,
            count=_READ_BATCH,
        )

        if not entries:
            entries = await consumer.read(
                CANDLE_STREAM,
                CANDLE_GROUP,
                name,
                count=_READ_BATCH,
                block_ms=_BLOCK_MS,
            )

        if not entries:
            continue

        report, acked = await closes.consume(entries)

        if acked:
            await consumer.ack(CANDLE_STREAM, CANDLE_GROUP, list(acked))

        log.info(
            "engine_batch_processed",
            received=report.received,
            processed=report.processed,
            failed=report.failed,
        )


def main() -> None:
    settings: EngineSettings = get_settings("engine")
    bootstrap(settings, "engine")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        db = build_engine(settings.db_dsn)
        redis_client = aioredis.from_url(settings.redis_url)

        pipeline = build_detection_pipeline(
            build_session_factory(db),
            redis_client,
            SystemClock(),
        )

        name = _consumer_name()

        task = asyncio.create_task(
            _consume_forever(
                RedisEventStreamConsumer(redis_client),
                CandleCloseConsumer(TrailingWindowRunner(pipeline)),
                name,
            )
        )

        app.state.consumer_task = task
        app.state.consumer_name = name

        try:
            yield
        finally:
            task.cancel()

            await asyncio.gather(task, return_exceptions=True)

            await redis_client.aclose()
            await db.dispose()

    app = build_health_app(settings)
    app.router.lifespan_context = lifespan

    run_asgi(app, settings.health_port)


if __name__ == "__main__":
    main()
