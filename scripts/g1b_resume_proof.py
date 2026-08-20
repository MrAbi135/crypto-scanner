"""G1b evidence: kill -9 the engine and prove no candle close is lost.

Roadmap §9 asks for "kill -9 on the engine loses no closes (resume proven, not
assumed)". Proven means run it and read the numbers, so this does that against
the live dev stack:

1.  wait until the consumer group is quiet, so the run starts from zero rather
    than from someone else's backlog;
2.  publish N candle-close entries onto the stream the engine consumes;
3.  wait until the group's delivery cursor has passed the last of them -- the
    engine now holds our work, unacknowledged;
4.  `docker kill -s KILL` the engine, mid-batch and with no chance to clean up;
5.  restart it;
6.  wait for the pending list to empty, which is only possible if the restarted
    process picked the interrupted batch back up.

**Delivery cursor, not pending count.** The engine acknowledges a whole batch
at once, so while it grinds through one the pending count sits flat -- which is
indistinguishable from "our entries were never picked up". An earlier version
of this script measured the count and concluded exactly that, wrongly.

The closes must land on candles the database actually holds. Anchored anywhere
else, every detection pass returns on an empty window in milliseconds, there is
no in-flight work to interrupt, and the run would still print PASSED.

Run (either shell, from the repo root):

    uv run --project backend python scripts/g1b_resume_proof.py

Exit code 0 means the proof held; anything else names the step that failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import redis.asyncio as aioredis

from scanner.application.ports.event_consumer import CANDLE_GROUP
from scanner.application.ports.event_stream import CANDLE_STREAM

DEFAULT_REDIS = "redis://localhost:6379/0"
DEFAULT_CONTAINER = "scanner-dev-engine-1"

SYMBOL = "BTCUSDT"
TIMEFRAME = "H1"

# Inside the seeded H1 range, so each pass has a real window to work over.
DEFAULT_ANCHOR = "2026-08-17T00:00:00+00:00"

# A pass over a full trailing window takes on the order of a minute, and the
# engine may already be working through an earlier batch.
DEFAULT_PICKUP_TIMEOUT = 900.0


def _log(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)


def _fail(message: str) -> None:
    print(f"\nFAILED: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _entry_key(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")

    return int(ms), int(seq or 0)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _pending_count(client: aioredis.Redis) -> int:
    try:
        summary = await client.xpending(CANDLE_STREAM, CANDLE_GROUP)
    except Exception:
        return 0

    if not summary:
        return 0

    return int(summary["pending"] if isinstance(summary, dict) else summary[0])


async def _last_delivered(client: aioredis.Redis) -> str | None:
    for group in await client.xinfo_groups(CANDLE_STREAM):
        if _text(group.get("name")) != CANDLE_GROUP:
            continue

        return _text(group.get("last-delivered-id"))

    return None


async def _publish(client: aioredis.Redis, count: int, anchor: datetime) -> list[str]:
    ids: list[str] = []

    for index in range(count):
        payload = {
            "payload": {
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "open_time": (anchor - timedelta(hours=index)).isoformat(),
            }
        }

        entry_id = await client.xadd(
            CANDLE_STREAM,
            {"event_id": f"g1b-{index}", "payload": json.dumps(payload)},
        )

        ids.append(_text(entry_id))

    return ids


async def _wait_until(check, *, timeout_s: float, interval_s: float = 2.0):
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        value = await check()

        if value is not None:
            return value

        await asyncio.sleep(interval_s)

    return None


async def _idle(client: aioredis.Redis) -> bool | None:
    return True if await _pending_count(client) == 0 else None


async def _delivered_through(client: aioredis.Redis, target: str) -> bool | None:
    current = await _last_delivered(client)

    if current is None:
        return None

    return True if _entry_key(current) >= _entry_key(target) else None


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


async def main() -> None:
    parser = argparse.ArgumentParser(description="G1b kill -9 resume proof")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--anchor", default=DEFAULT_ANCHOR)
    parser.add_argument("--pickup-timeout", type=float, default=DEFAULT_PICKUP_TIMEOUT)
    parser.add_argument("--drain-timeout", type=float, default=900.0)

    args = parser.parse_args()

    client = aioredis.from_url(args.redis_url)

    try:
        backlog = await _pending_count(client)

        if backlog:
            _log("1/6", f"{backlog} entries already pending -- waiting for the engine to finish")

            settled = await _wait_until(
                lambda: _idle(client),
                timeout_s=args.pickup_timeout,
                interval_s=5.0,
            )

            if settled is None:
                _fail(
                    f"the engine still holds {await _pending_count(client)} entries after "
                    f"{args.pickup_timeout:.0f}s. Start from a quiet stream, or raise "
                    "--pickup-timeout: one pass takes about a minute."
                )

        _log("1/6", "stream is quiet: nothing pending")

        published = await _publish(client, args.count, datetime.fromisoformat(args.anchor))

        _log("2/6", f"published {len(published)} candle-close entries")

        delivered = await _wait_until(
            lambda: _delivered_through(client, published[-1]),
            timeout_s=args.pickup_timeout,
        )

        if delivered is None:
            _fail(
                "the group's delivery cursor never reached our last entry within "
                f"{args.pickup_timeout:.0f}s. Check `docker logs` for "
                "detection_pass_completed before raising the timeout."
            )

        in_flight = await _pending_count(client)

        if in_flight == 0:
            _fail(
                "the engine finished every entry before the kill landed, so this run "
                "interrupted nothing. Raise --count so a batch is still in flight."
            )

        _log("3/6", f"engine holds {in_flight} entries unacked -- killing it mid-batch")

        killed = _docker("kill", "-s", "KILL", args.container)

        if killed.returncode != 0:
            _fail(f"docker kill failed: {killed.stderr.strip()}")

        _log("4/6", f"SIGKILL delivered to {args.container}")

        started = _docker("start", args.container)

        if started.returncode != 0:
            _fail(f"docker start failed: {started.stderr.strip()}")

        _log("5/6", "engine restarted; waiting for the pending list to drain")

        drained = await _wait_until(
            lambda: _idle(client),
            timeout_s=args.drain_timeout,
        )

        if drained is None:
            _fail(
                f"pending list did not drain: {await _pending_count(client)} entries still "
                f"unacked after {args.drain_timeout:.0f}s. Closes were lost or stalled."
            )

        _log("6/6", "pending list empty -- every close the kill interrupted was redone")

        print("\nG1b resume proof: PASSED")
        print(f"  published       : {len(published)}")
        print(f"  unacked at kill : {in_flight}")
        print(f"  pending after   : {await _pending_count(client)}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
