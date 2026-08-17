"""Transactional outbox port (T39, TAD §12).

The outbox exists to close one gap: a candle lands in Postgres and the process
dies before telling Redis, or Redis is told about a candle whose transaction
rolled back. Either way the engine's view of the market diverges from the
database's, silently and permanently.

So the event is written **in the same transaction as the candle**. That is why
there is no `append` method here: appending is not a thing a caller does on its
own, it is something `CandleRepository.bulk_insert` does atomically with the
insert. This port covers only the second half -- the relay reading what was
committed and pushing it to the stream.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

CANDLE_CLOSED_EVENT = "market.candle.closed"
CANDLE_AGGREGATE = "candle"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One committed, not-yet-relayed event."""

    id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: str
    created_at: datetime
    relay_attempts: int


class OutboxRepository(Protocol):
    async def claim_unrelayed(
        self,
        limit: int,
    ) -> Sequence[OutboxRecord]:
        """Oldest-first unrelayed events, locked against a concurrent relay.

        Ordered by `created_at` so the stream sees closes in the order they
        were committed. A relay that skipped ahead would hand the engine an
        H1 close before the H4 close that preceded it.
        """
        ...

    async def mark_relayed(
        self,
        ids: Sequence[str],
        *,
        relayed_at: datetime,
    ) -> int:
        """Mark events delivered. Already-relayed ids are left untouched."""
        ...

    async def record_relay_failure(
        self,
        ids: Sequence[str],
    ) -> int:
        """Increment `relay_attempts` after a failed delivery.

        Attempts are counted rather than used to drop events: at-least-once is
        the contract, and the engine's persistence uniqueness makes a duplicate
        harmless. A rising count is an alarm, not a discard rule.
        """
        ...
