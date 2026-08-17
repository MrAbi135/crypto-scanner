"""Redis Streams consumer group (Sprint S4b, TAD §12)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from scanner.application.ports.event_consumer import StreamEntry


class RedisEventStreamConsumer:
    def __init__(
        self,
        client: aioredis.Redis,
    ) -> None:
        self._client = client

    async def ensure_group(
        self,
        stream: str,
        group: str,
    ) -> None:
        try:
            # mkstream because the engine may boot before ingest has produced
            # anything; without it the first start fails on a missing key and
            # the process crash-loops until a candle happens to close.
            await self._client.xgroup_create(
                stream,
                group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            # Redis has no "create if absent" for groups. BUSYGROUP is the
            # success case on every boot after the first, so it is matched
            # narrowly rather than swallowing ResponseError wholesale.
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        block_ms: int,
    ) -> Sequence[StreamEntry]:
        response = await self._client.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=count,
            block=block_ms,
        )

        return _flatten(response)

    async def claim_stale(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int,
    ) -> Sequence[StreamEntry]:
        # XAUTOCLAIM rather than XPENDING+XCLAIM: one round trip, and it skips
        # entries whose underlying stream data has been trimmed away instead of
        # handing back tombstones.
        _, entries, _ = await self._client.xautoclaim(
            stream,
            group,
            consumer,
            min_idle_time=min_idle_ms,
            count=count,
        )

        return tuple(
            StreamEntry(entry_id=_text(entry_id), fields=_decode(fields))
            for entry_id, fields in entries
        )

    async def ack(
        self,
        stream: str,
        group: str,
        entry_ids: Sequence[str],
    ) -> int:
        if not entry_ids:
            return 0

        return int(await self._client.xack(stream, group, *entry_ids))


def _flatten(response: Any) -> tuple[StreamEntry, ...]:
    """xreadgroup returns [(stream, [(id, fields), ...]), ...], or None on timeout."""
    if not response:
        return ()

    flattened: list[StreamEntry] = []

    for _, entries in response:
        for entry_id, fields in entries:
            flattened.append(
                StreamEntry(
                    entry_id=_text(entry_id),
                    fields=_decode(fields),
                )
            )

    return tuple(flattened)


def _decode(fields: dict[object, object]) -> dict[str, str]:
    return {_text(key): _text(value) for key, value in fields.items()}


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)
