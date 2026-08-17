"""Drive detection from candle-close events (Sprint S4b).

This is the piece the original S4 was closed without: the thing that makes
detection happen on its own. Everything it calls already existed and had only
ever been reachable by typing `engine run --symbol … --start … --end …`.

Two ordering rules from the S4 spec are enforced here rather than left to the
stream, because a consumer group makes no promises about either:

* **HTF first.** A higher timeframe's structure is context for the lower ones,
  so within a batch H4 is processed before H1 before M5. Handing the engine an
  M5 close first would have it read an H4 picture that is one bar stale.
* **Per-context sequential.** Closes for one (symbol, timeframe) run in
  open_time order, one at a time. Concurrency across *different* contexts is
  safe; within one it is a race over the same state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import structlog

from scanner.application.ports.event_consumer import StreamEntry
from scanner.shared import Timeframe
from scanner.shared.errors import DomainInvariantError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CandleClose:
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    entry_id: str


@dataclass(frozen=True, slots=True)
class ConsumeReport:
    received: int
    processed: int
    acked: int
    failed: int


class DetectionRunner(Protocol):
    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        open_time: datetime,
    ) -> None:
        """Run every detector for this context, up to and including this close."""
        ...


def parse_close(entry: StreamEntry) -> CandleClose:
    """Read a stream entry back into the close it announces.

    Raises rather than returning None: the relay is a pipe that copies the
    outbox payload verbatim, so anything unparseable here means the payload
    written in `PgCandleRepository` and the reader below have drifted apart.
    Skipping it would drop a real close and leave the entry acked.
    """
    raw = entry.fields.get("payload")

    if not raw:
        raise DomainInvariantError(
            "stream entry carries no payload",
            details={"entry_id": entry.entry_id},
        )

    try:
        payload = json.loads(raw)["payload"]

        return CandleClose(
            symbol=payload["symbol"],
            timeframe=Timeframe(payload["timeframe"]),
            open_time=datetime.fromisoformat(payload["open_time"]),
            entry_id=entry.entry_id,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise DomainInvariantError(
            "stream entry payload does not describe a candle close",
            details={"entry_id": entry.entry_id},
        ) from exc


def order_for_detection(closes: Sequence[CandleClose]) -> tuple[CandleClose, ...]:
    """HTF first, then chronological within a context.

    Sorting the whole batch by (-duration, symbol, open_time) achieves both at
    once: every H4 close precedes every H1 close, and within any one context
    the closes stay in the order the market produced them.
    """
    return tuple(
        sorted(
            closes,
            key=lambda close: (
                -close.timeframe.duration.total_seconds(),
                close.symbol,
                close.open_time,
            ),
        )
    )


class CandleCloseConsumer:
    """Turn stream entries into detection passes."""

    def __init__(
        self,
        runner: DetectionRunner,
    ) -> None:
        self._runner = runner

    async def consume(
        self,
        entries: Sequence[StreamEntry],
    ) -> tuple[ConsumeReport, tuple[str, ...]]:
        """Process a batch. Returns the report and the ids safe to acknowledge.

        A context that raises does **not** have its entry acked, so it returns
        on the next claim. One bad context must not cost the others their run,
        which is why the loop continues rather than aborting the batch.
        """
        if not entries:
            return ConsumeReport(0, 0, 0, 0), ()

        closes = order_for_detection([parse_close(entry) for entry in entries])

        acked: list[str] = []
        failed = 0

        for close in closes:
            try:
                await self._runner.run(
                    close.symbol,
                    close.timeframe,
                    close.open_time,
                )
            except Exception:
                failed += 1

                # Logged, not swallowed: the entry stays pending and will be
                # retried, so this is a recorded retry rather than a silent
                # failure. Re-raising would strand the rest of the batch.
                log.exception(
                    "detection_pass_failed",
                    symbol=close.symbol,
                    timeframe=close.timeframe.value,
                    open_time=close.open_time.isoformat(),
                    entry_id=close.entry_id,
                )

                continue

            acked.append(close.entry_id)

        return (
            ConsumeReport(
                received=len(entries),
                processed=len(acked),
                acked=len(acked),
                failed=failed,
            ),
            tuple(acked),
        )
