"""Event stream consumption port (TAD §12, Sprint S4b).

A consumer group, not a plain read, because the engine must be able to die and
come back without losing closes. Entries stay pending until acknowledged, and
`claim_stale` is how a restarted process reclaims what the previous one had
taken but never finished.

Delivery is at-least-once by design -- see `application/marketdata/outbox_relay`
for why that direction was chosen. Consumers must therefore be idempotent; in
this system that is provided by persistence uniqueness on every detection write,
not by anything here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

CANDLE_GROUP = "engine"


@dataclass(frozen=True, slots=True)
class StreamEntry:
    entry_id: str
    fields: Mapping[str, str]


class EventStreamConsumer(Protocol):
    async def ensure_group(
        self,
        stream: str,
        group: str,
    ) -> None:
        """Create the group if absent. Idempotent; safe on every boot."""
        ...

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        block_ms: int,
    ) -> Sequence[StreamEntry]:
        """Block for up to `block_ms` waiting for undelivered entries."""
        ...

    async def drain_pending(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
    ) -> Sequence[StreamEntry]: ...

    async def claim_stale(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int,
    ) -> Sequence[StreamEntry]:
        """Take over entries delivered to a consumer that never acked them.

        Without this, a `kill -9` mid-batch strands those entries in the
        pending list forever: they are delivered, so `read` will not return
        them again, and unacked, so nothing else will either. The candles
        would exist, the events would exist, and detection would never run.
        """
        ...

    async def ack(
        self,
        stream: str,
        group: str,
        entry_ids: Sequence[str],
    ) -> int:
        """Acknowledge processed entries, removing them from the pending list."""
        ...
