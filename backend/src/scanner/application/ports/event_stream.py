"""Event stream publication port (TAD §12).

The relay's downstream half. Kept separate from `OutboxRepository` because the
two halves fail differently and independently: the database read can succeed
while the stream write fails, which is precisely the case the outbox exists to
survive.

**Ordering.** Entries are appended in the order given, and a single relay reads
the outbox in monotonic id order, so the stream reflects commit order. That is
a useful property but not the load-bearing one: a consumer *group* hands
different entries to different workers, so nothing about the stream guarantees
that two closes for one symbol are processed in sequence. Per-context ordering
is the engine's obligation, not the stream's. Do not build on stream order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

CANDLE_STREAM = "scanner:stream:candle-closed"


class EventStreamPublisher(Protocol):
    async def publish(
        self,
        stream: str,
        entries: Sequence[Mapping[str, str]],
    ) -> int:
        """Append entries to the stream, in order. Returns the count appended.

        Raises rather than returning a partial count on failure: the relay
        must not mark anything relayed on a write it cannot vouch for, and a
        silently short write would do exactly that.
        """
        ...
