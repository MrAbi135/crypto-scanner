"""§6.4's depth input for the engines that run the abnormal-volume check.

Participation (which records VOLUME_SUSPECT) and confluence (which scores the
newest candle) both run §6.4's check, and they must reach the same verdict
from the same evidence. Both read the depth through this one class, so the
choice of snapshot lives in `domain.volume.depth` alone.

One query per symbol per day: the readable snapshots only change when a
candle's close crosses the start of a UTC day, so the rows are cached per
symbol under that day. The cache holds one entry per symbol and is replaced,
never grown.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
    LiquidityHistoryRepository,
)
from scanner.domain.volume.depth import (
    DEPTH_MEDIAN_SNAPSHOTS,
    DepthReading,
    DepthSnapshot,
    depth_at,
    latest_readable_stamp,
)

_DAY = timedelta(days=1)


def _day_start(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


class DepthInput:
    """The depth and 7-day median a candle may read, or None.

    Absent a repository (the golden harness without a depth history, a unit
    test) every reading is None, which is what production reports for a symbol
    the daily job has not yet seen: the check is then incomplete, not failed.
    """

    def __init__(self, history: LiquidityHistoryRepository | None) -> None:
        self._history = history
        self._cache: dict[str, tuple[datetime, Sequence[LiquidityHistoryRecord]]] = {}

    async def at(self, symbol: str, close_time: datetime) -> DepthReading | None:
        if self._history is None:
            return None

        day = _day_start(latest_readable_stamp(close_time))
        cached = self._cache.get(symbol)

        if cached is None or cached[0] != day:
            # Rows are stamped at a day's start (one per symbol per day), so
            # every stamp readable anywhere in `day` is at or before the next
            # day's start: one row more than the median needs covers it, and
            # `depth_at` drops whatever this candle may not read.
            rows = await self._history.fetch_until(
                symbol, day + _DAY, limit=DEPTH_MEDIAN_SNAPSHOTS + 1
            )
            cached = (day, rows)
            self._cache[symbol] = cached

        return depth_at(
            [DepthSnapshot(r.observed_at, r.depth_2pct) for r in cached[1]],
            close_time,
        )
