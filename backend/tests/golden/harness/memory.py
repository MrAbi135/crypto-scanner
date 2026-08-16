"""In-memory port implementations that let golden datasets drive real services.

The detection services depend on *ports*, not on Postgres or Redis, so a
golden run can feed them from a file and still execute the production code
path byte for byte. That is the point: the harness must not re-implement
doctrine, or it would only ever agree with itself.

Everything here is deliberately strict — no autovivification, no silent
coercion — because a permissive double hides the very bugs a golden dataset
exists to catch.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from scanner.application.ports.detection import EngineEventRecord
from scanner.domain.common import Candle
from scanner.shared import Timeframe


class FixedClock:
    """A clock frozen at one instant.

    Detection output must not depend on wall time. Freezing the clock is how
    the harness proves that: if a detector ever branched on "now", two runs
    would still agree here, but the determinism property would be a lie. The
    canonical form therefore also drops clock-derived fields.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class InMemoryCandleRepository:
    """Serves one immutable, pre-sorted candle series."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = tuple(candles)

    async def bulk_insert(self, candles: Sequence[Candle]) -> int:
        raise NotImplementedError("golden datasets are read-only inputs")

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None:
        series = self._series(symbol, timeframe)
        return series[-1].open_time if series else None

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        return tuple(
            candle for candle in self._series(symbol, timeframe) if start <= candle.open_time < end
        )

    async def count_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int:
        return len(await self.fetch_series(symbol, timeframe, start, end))

    def _series(self, symbol: str, timeframe: Timeframe) -> tuple[Candle, ...]:
        return tuple(
            candle
            for candle in self._candles
            if candle.symbol == symbol and candle.timeframe is timeframe
        )


class InMemoryEngineEventRepository:
    """Records emitted events, mirroring the real ON CONFLICT DO NOTHING key.

    The production table is unique on ``event_key``, and services rely on
    ``append`` returning False for a duplicate to keep replay idempotent. The
    double reproduces exactly that contract.
    """

    def __init__(self) -> None:
        self.events: list[EngineEventRecord] = []
        self._keys: set[str] = set()

    async def append(self, event: EngineEventRecord) -> bool:
        if event.event_key in self._keys:
            return False

        self._keys.add(event.event_key)
        self.events.append(event)
        return True

    async def exists(self, event_key: str) -> bool:
        return event_key in self._keys


class InMemoryEngineStateStore:
    """Key/value snapshot store standing in for Redis."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def load(self, context_key: str) -> str | None:
        return self.values.get(context_key)

    async def save(self, context_key: str, payload: str) -> None:
        self.values[context_key] = payload

    async def delete(self, context_key: str) -> None:
        self.values.pop(context_key, None)
