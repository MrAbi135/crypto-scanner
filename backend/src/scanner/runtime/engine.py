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
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from redis.exceptions import RedisError
from starlette.applications import Starlette

from scanner.application.detection.candle_close_consumer import CandleCloseConsumer
from scanner.application.detection.confluence_replay import CONFLUENCE_ALGO_VERSION
from scanner.application.detection.trailing_window import TrailingWindowRunner
from scanner.application.param_set_verification import verify_parameter_set
from scanner.application.ports.event_consumer import CANDLE_GROUP
from scanner.application.ports.event_stream import CANDLE_STREAM
from scanner.config import get_settings
from scanner.config.processes import EngineSettings
from scanner.infrastructure.clock import SystemClock
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.param_set_repository import (
    PgParamSetRepository,
)
from scanner.infrastructure.redis.client import build_redis
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

# Long enough not to hammer a Redis that is genuinely down, short enough that a
# recovered one is picked up before the next candle closes.
_RECONNECT_BACKOFF_S = 5


def _consumer_name() -> str:
    """Stable per-container identity.

    The pending list is keyed by consumer name, so a restarted process must
    present the same one to find its own unfinished work at all. A random name
    each boot would strand the previous consumer's entries until the idle claim
    swept them a minute later.
    """
    return f"engine-{os.getenv('HOSTNAME', 'local')}"


async def _consume_forever(
    consumer: RedisEventStreamConsumer,
    closes: CandleCloseConsumer,
    name: str,
) -> None:
    await consumer.ensure_group(CANDLE_STREAM, CANDLE_GROUP)

    # Whatever this consumer name left unacked last time it ran. A read for ">"
    # will never return it -- that means "not yet delivered to anyone" -- so
    # without this pass a crashed batch waits out `_CLAIM_IDLE_MS` before
    # anything touches it.
    recovered = await consumer.drain_pending(
        CANDLE_STREAM,
        CANDLE_GROUP,
        name,
        count=_READ_BATCH,
    )

    log.info(
        "engine_consumer_started",
        consumer=name,
        stream=CANDLE_STREAM,
        recovered=len(recovered),
    )

    if recovered:
        report, acked = await closes.consume(recovered)

        if acked:
            await consumer.ack(CANDLE_STREAM, CANDLE_GROUP, list(acked))

        log.info(
            "engine_resume_processed",
            received=report.received,
            processed=report.processed,
            failed=report.failed,
        )

    while True:
        try:
            await _consume_once(consumer, closes, name)
        except RedisError:
            # A blip, not a bug. The engine holds a connection across a batch
            # that can run for minutes, so a reconnect mid-run is expected over
            # 72 hours -- and killing the process for one would turn every
            # network hiccup into an outage. Unacked entries stay pending and
            # come back; that is what the consumer group is for.
            log.warning("engine_redis_unavailable", exc_info=True)

            await asyncio.sleep(_RECONNECT_BACKOFF_S)


async def _consume_once(
    consumer: RedisEventStreamConsumer,
    closes: CandleCloseConsumer,
    name: str,
) -> None:
    """One claim-read-process-ack cycle."""
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
        return

    report, acked = await closes.consume(entries)

    if acked:
        await consumer.ack(CANDLE_STREAM, CANDLE_GROUP, list(acked))

    log.info(
        "engine_batch_processed",
        received=report.received,
        processed=report.processed,
        failed=report.failed,
    )


def _on_consumer_exit(task: asyncio.Task[None]) -> None:
    """Log why the consumer stopped, and stop the process with it.

    Cancellation during shutdown is the one benign case. Anything else means
    the engine has no reason to keep running: its only job was the loop that
    just died, and a live process without it is worse than a dead one because
    nothing is alerted.
    """
    if task.cancelled():
        return

    error = task.exception()

    if error is None:
        log.error("engine_consumer_stopped", reason="loop returned")
    else:
        log.error("engine_consumer_crashed", exc_info=error)

    os.kill(os.getpid(), signal.SIGTERM)


def main() -> None:
    settings: EngineSettings = get_settings("engine")
    bootstrap(settings, "engine")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        db = build_engine(settings.db_dsn)
        redis_client = build_redis(settings.redis_url)

        sessions = build_session_factory(db)
        clock = SystemClock()

        # TAD §14, before anything is built that could score with the wrong
        # numbers: "param-set checksum mismatch => engine refuses to score".
        # `verify_parameter_set` raises here and the lifespan never opens, so
        # the container dies with a precise reason instead of coming up
        # healthy and scoring under a parameter set nobody recorded.
        await verify_parameter_set(
            PgParamSetRepository(sessions),
            engine="detection",
            algo_version=CONFLUENCE_ALGO_VERSION,
            now=clock.now(),
            sls_reference="Appendix A",
        )

        pipeline = build_detection_pipeline(
            sessions,
            redis_client,
            clock,
        )

        name = _consumer_name()

        task = asyncio.create_task(
            _consume_forever(
                RedisEventStreamConsumer(redis_client),
                CandleCloseConsumer(TrailingWindowRunner(pipeline)),
                name,
            )
        )

        # A bare `create_task` stores the exception on the task object and
        # tells no one. The process then keeps serving health checks with a
        # dead consumer inside it -- container up, readiness green, and not a
        # single candle close processed. That is the exact failure G1b's "runs
        # unattended >= 72 h" would sit through and report as a pass.
        task.add_done_callback(_on_consumer_exit)

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
