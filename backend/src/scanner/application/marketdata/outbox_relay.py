"""Outbox relay: committed events -> Redis Stream (Sprint S4b, TAD §12).

One sweep is: claim a batch, write it to the stream, mark it relayed. The order
of those three steps is the whole design, and it is deliberately the one that
can duplicate rather than the one that can lose.

If the process dies after the stream write but before `mark_relayed`, the next
sweep sees the same rows unmarked and delivers them again. That is at-least-
once, and it is safe because every detection write is idempotent through
persistence uniqueness. The alternative -- mark first, publish second -- would
turn the same crash into an event nobody ever receives, for a candle that
exists, with nothing anywhere recording that a close went unprocessed.

Losing an event is silent and permanent. Duplicating one is absorbed one layer
down. So the relay duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.ports import Clock
from scanner.application.ports.event_stream import (
    CANDLE_STREAM,
    EventStreamPublisher,
)
from scanner.application.ports.outbox import (
    CANDLE_CLOSED_EVENT,
    OutboxRepository,
)

DEFAULT_BATCH = 200


@dataclass(frozen=True, slots=True)
class RelaySweepReport:
    claimed: int
    published: int
    marked: int
    failed: int


class OutboxRelayService:
    """Drain committed outbox events onto the stream."""

    def __init__(
        self,
        outbox: OutboxRepository,
        publisher: EventStreamPublisher,
        clock: Clock,
        *,
        batch_size: int = DEFAULT_BATCH,
        stream: str = CANDLE_STREAM,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        self._outbox = outbox
        self._publisher = publisher
        self._clock = clock
        self._batch_size = batch_size
        self._stream = stream

    async def sweep(self) -> RelaySweepReport:
        """Relay one batch. Returns what happened, for the caller to log."""

        claimed = await self._outbox.claim_unrelayed(self._batch_size)

        if not claimed:
            return RelaySweepReport(
                claimed=0,
                published=0,
                marked=0,
                failed=0,
            )

        # Only candle closes have a stream today. Anything else committed to
        # the outbox would sit unrelayed forever while looking healthy, so it
        # is refused loudly at the point the assumption breaks rather than
        # quietly skipped here.
        unknown = [record for record in claimed if record.event_type != CANDLE_CLOSED_EVENT]

        if unknown:
            raise ValueError(
                f"no stream is configured for event types: "
                f"{sorted({record.event_type for record in unknown})}"
            )

        entries = [
            {
                "event_id": record.id,
                "event_type": record.event_type,
                "aggregate_id": record.aggregate_id,
                "payload": record.payload,
            }
            for record in claimed
        ]

        ids = [record.id for record in claimed]

        try:
            published = await self._publisher.publish(
                self._stream,
                entries,
            )
        except Exception:
            # Counted, not dropped. A rising relay_attempts is the signal that
            # Redis is unreachable; the events stay queued and are retried.
            await self._outbox.record_relay_failure(ids)
            raise

        marked = await self._outbox.mark_relayed(
            ids,
            relayed_at=self._clock.now(),
        )

        return RelaySweepReport(
            claimed=len(claimed),
            published=published,
            marked=marked,
            failed=0,
        )
