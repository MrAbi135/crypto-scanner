"""Which contexts can actually produce detections (Sprint S3b, SLS §1.9).

`verify_continuity` answers "is this series hole-free". That is a different
question from "will the engine emit anything if a candle closes on it", and
until now nothing answered the second one.

It matters because a cold context fails **silently and identically to a healthy
idle one**: the engine consumes the close, the warm-up gate declines, no event
is written, and no error is raised anywhere. A week of that looks exactly like a
week of quiet markets. This module is the difference between the two.

The 14-day listing half of §1.9 is not assessed here. `market.symbols` carries
`first_seen_at`, which is when *we* first saw the symbol, not when the venue
listed it -- see `domain/common/warmup`. Reporting a listing check against the
wrong date would be worse than reporting none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from scanner.application.ports import CandleRepository
from scanner.domain.common.warmup import (
    DETECTION_MIN_CANDLES,
    VOLUME_MOMENTUM_MIN_CANDLES,
    WarmupCapability,
    is_warm,
)
from scanner.shared import Timeframe

# Every timeframe the platform scans. Deliberately the whole enum rather than a
# separate list: Timeframe *is* the SLS §0.2 scanned set, and a second copy would
# be a place for the two to disagree. A timeframe nobody ingests shows up EMPTY,
# which is information rather than noise.
ENGINE_TIMEFRAMES: tuple[Timeframe, ...] = tuple(Timeframe)

# Nothing in the record predates the venue, so this is simply "from the
# beginning" without pretending to know when that was per symbol.
_EPOCH = datetime.fromisoformat("2017-01-01T00:00:00+00:00")


@dataclass(frozen=True, slots=True)
class ContextWarmth:
    symbol: str
    timeframe: Timeframe
    closed_candles: int

    @property
    def detection_warm(self) -> bool:
        return is_warm(
            WarmupCapability.DETECTION,
            closed_candles=self.closed_candles,
        )

    @property
    def volume_warm(self) -> bool:
        return is_warm(
            WarmupCapability.VOLUME,
            closed_candles=self.closed_candles,
        )

    @property
    def candles_short(self) -> int:
        """How many more closes before structure/liquidity/ICT will run."""
        return max(0, DETECTION_MIN_CANDLES - self.closed_candles)

    def describe(self) -> str:
        if self.detection_warm:
            return "WARM"

        if self.volume_warm:
            return f"VOLUME_ONLY (needs {self.candles_short} more for detection)"

        if self.closed_candles == 0:
            return "EMPTY"

        return (
            f"COLD (needs {self.candles_short} more for detection, "
            f"{max(0, VOLUME_MOMENTUM_MIN_CANDLES - self.closed_candles)} for volume)"
        )


async def assess_context(
    candles: CandleRepository,
    symbol: str,
    timeframe: Timeframe,
    *,
    now: datetime,
) -> ContextWarmth:
    """Count the closed candles behind one context and grade it."""

    # `now` bounds the count at the present rather than at the newest row, so a
    # series that stopped updating a month ago is still reported by how much
    # history it holds -- staleness is `FreshnessTracker`'s question, not this
    # module's, and conflating them would hide one behind the other.
    stored = await candles.count_series(
        symbol,
        timeframe,
        _EPOCH,
        now,
    )

    return ContextWarmth(
        symbol=symbol,
        timeframe=timeframe,
        closed_candles=stored,
    )


async def assess_all(
    candles: CandleRepository,
    contexts: tuple[tuple[str, Timeframe], ...],
    *,
    now: datetime,
) -> tuple[ContextWarmth, ...]:
    return tuple(
        [
            await assess_context(candles, symbol, timeframe, now=now)
            for symbol, timeframe in contexts
        ]
    )
