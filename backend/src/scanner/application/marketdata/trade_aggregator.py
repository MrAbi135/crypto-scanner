"""Fold a live aggTrade stream into DDD T4 minute buckets (SLS §2.2).

The stream delivers prints; the record is the minute. Everything here exists
to get from one to the other without ever writing a minute that is still
happening, because SLS §2 does not keep the prints:

    Raw individual ticks are **not** retained beyond aggregation in v1.

A bucket written early cannot be corrected later. There is nothing to
recompute it from.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from scanner.application.ports import Clock
from scanner.application.ports.repositories import TradeAggregateRepository
from scanner.domain.common import TradePrint, aggregate_minute, minute_of

log = structlog.get_logger(__name__)


class TradeAggregator:
    """Buffer prints per symbol and persist each minute once it is over."""

    def __init__(
        self,
        repository: TradeAggregateRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._buffers: dict[str, dict[datetime, list[TradePrint]]] = {}
        self._sealed: dict[str, datetime] = {}
        self._dropped: dict[str, int] = {}

    async def observe(self, symbol: str, print_: TradePrint) -> int:
        """Buffer one print, flushing any minute it proves is finished.

        A print in minute M is evidence that every earlier minute has ended,
        which is the only signal the stream gives -- there is no end-of-minute
        frame.
        """
        minute = minute_of(print_.at)

        sealed = self._sealed.get(symbol)

        if sealed is not None and minute <= sealed:
            # Reconnects replay. A print for a minute already written cannot
            # change it, so it is dropped -- and counted, because a silent
            # drop and a stream that never reconnected look identical.
            self._dropped[symbol] = self._dropped.get(symbol, 0) + 1

            return 0

        self._buffers.setdefault(symbol, {}).setdefault(minute, []).append(print_)

        return await self._flush_before(symbol, minute)

    async def flush_completed(self, symbol: str) -> int:
        """Persist every minute that is over by the clock.

        A symbol that stops printing would otherwise hold its last minute
        forever: `observe` only ever learns a minute ended from the print that
        started the next one, and a quiet market sends none.
        """
        return await self._flush_before(symbol, minute_of(self._clock.now()))

    def dropped(self, symbol: str) -> int:
        """Prints discarded as belonging to an already-written minute."""
        return self._dropped.get(symbol, 0)

    async def _flush_before(self, symbol: str, boundary: datetime) -> int:
        buffered = self._buffers.get(symbol)

        if not buffered:
            return 0

        due = sorted(minute for minute in buffered if minute < boundary)

        if not due:
            return 0

        aggregates = []

        for minute in due:
            aggregate = aggregate_minute(symbol, minute, buffered.pop(minute))

            if aggregate is not None:
                aggregates.append(aggregate)

        inserted = await self._repository.append_many(aggregates)

        self._sealed[symbol] = max(self._sealed.get(symbol, due[0]), due[-1])

        log.info(
            "trade_minutes_persisted",
            symbol=symbol,
            minutes=len(aggregates),
            inserted=inserted,
            prints=sum(item.trade_count for item in aggregates),
            dropped_total=self._dropped.get(symbol, 0),
        )

        return inserted
