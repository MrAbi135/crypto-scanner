"""One read of a pass's candle window, shared by every engine in the pass.

Each of the nine services in `DetectionPipeline` reads its window through
`CandleRepository.fetch_series`, and on a live close they all ask for the same
one -- so a pass read the same 500 candles from Postgres about nine times and
built ~4,500 `Candle` objects to use 500. Measured on 2026-10-11 against real
BTCUSDT candles: a third of a pass's time after the liquidity writes were
batched.

The cache lives only inside `scope()`, which the pipeline enters once per
pass. Outside a scope every call goes straight through, so the golden harness,
`engine run` and any other caller see exactly what they saw before. Inside it,
an identical (symbol, timeframe, start, end) is served from the first read --
every service in the pass then judges the same candles, where before a
revision landing mid-pass could hand two services different ones.

The scope is a `ContextVar`, not an attribute, so two passes running as
separate asyncio tasks each get their own cache.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal

from scanner.application.ports.repositories import CandleRepository
from scanner.domain.common import Candle
from scanner.shared import Timeframe

_SeriesKey = tuple[str, Timeframe, datetime, datetime]

_PASS: ContextVar[dict[_SeriesKey, tuple[Candle, ...]] | None] = ContextVar(
    "detection_pass_candles", default=None
)


class PassScopedCandles:
    """A `CandleRepository` whose `fetch_series` is read once per pass."""

    def __init__(self, inner: CandleRepository) -> None:
        self._inner = inner

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[None]:
        token = _PASS.set({})

        try:
            yield
        finally:
            _PASS.reset(token)

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        cache = _PASS.get()

        if cache is None:
            return await self._inner.fetch_series(symbol, timeframe, start, end)

        key = (symbol, timeframe, start, end)

        if key not in cache:
            # A tuple, so no service can change what the next one is handed.
            cache[key] = tuple(await self._inner.fetch_series(symbol, timeframe, start, end))

        return cache[key]

    async def bulk_insert(
        self,
        candles: Sequence[Candle],
        *,
        emit_outbox: bool = False,
    ) -> int:
        return await self._inner.bulk_insert(candles, emit_outbox=emit_outbox)

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None:
        return await self._inner.latest_open_time(symbol, timeframe)

    async def newest_per_series(self) -> Sequence[tuple[str, Timeframe, datetime]]:
        return await self._inner.newest_per_series()

    async def fetch_volumes(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[tuple[datetime, Decimal]]:
        return await self._inner.fetch_volumes(symbol, timeframe, start, end)

    async def count_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int:
        return await self._inner.count_series(symbol, timeframe, start, end)
