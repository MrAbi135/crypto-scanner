"""Redis Streams publisher (Sprint S4b, TAD §12).

Cache registry entry: `scanner:stream:candle-closed` -- see docs/cache-registry.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import redis.asyncio as aioredis

# Trimmed approximately (`~`) because exact trimming forces Redis to scan macro
# node boundaries on every call. The cap is a safety ceiling against unbounded
# growth if consumers stall, not a retention policy -- the outbox is the record
# of what happened, the stream is only the delivery mechanism.
_MAX_LEN = 100_000


class RedisEventStreamPublisher:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        max_len: int = _MAX_LEN,
    ) -> None:
        self._client = client
        self._max_len = max_len

    async def publish(
        self,
        stream: str,
        entries: Sequence[Mapping[str, str]],
    ) -> int:
        if not stream:
            raise ValueError("stream must not be empty")

        if not entries:
            return 0

        # One pipeline, one round trip, order preserved. Not a transaction:
        # Redis Streams have no rollback, and the outbox is what makes a
        # partial append survivable -- unmarked rows are simply relayed again.
        pipeline = self._client.pipeline(transaction=False)

        for entry in entries:
            pipeline.xadd(
                stream,
                # redis-py types the field map as accepting bytes/int/float keys
                # too; ours are always str, which is a subset it cannot express.
                dict(entry),  # type: ignore[arg-type]
                maxlen=self._max_len,
                approximate=True,
            )

        results = await pipeline.execute()

        return len(results)
