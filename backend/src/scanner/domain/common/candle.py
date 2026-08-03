"""The canonical candle value object (SLS §2.1).

A Candle that exists is sane: construction enforces the §2.15 *intrinsic*
checks (single-candle shape). Series-level checks (alignment, continuity,
cross-TF) live in the validation battery, which sees whole batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from scanner.shared import Timeframe, require
from scanner.shared.timeutil import is_boundary

_ZERO = Decimal("0")


class CandleSource(str, Enum):
    """Provenance of the row (DDD T3)."""

    STREAM = "stream"
    BACKFILL = "backfill"
    REBUILT = "rebuilt"


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    taker_buy_volume: Decimal
    trade_count: int
    source: CandleSource

    def __post_init__(self) -> None:
        require(bool(self.symbol), "CANDLE_SYMBOL_EMPTY", "candle symbol must be non-empty")
        require(
            is_boundary(self.open_time, self.timeframe),
            "CANDLE_MISALIGNED",
            f"{self.symbol} {self.timeframe.value}: open_time {self.open_time.isoformat()} "
            f"is not a {self.timeframe.value} boundary",
        )
        require(
            all(v >= _ZERO for v in (self.open, self.high, self.low, self.close)),
            "CANDLE_NEGATIVE_PRICE",
            f"{self._key()}: negative price",
        )
        require(
            self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close),
            "CANDLE_OHLC_ORDER",
            f"{self._key()}: OHLC ordering violated (H≥max(O,C), L≤min(O,C) — SLS §2.15)",
        )
        require(self.high >= self.low, "CANDLE_HL_INVERTED", f"{self._key()}: high < low")
        require(
            self.volume >= _ZERO and self.quote_volume >= _ZERO,
            "CANDLE_NEGATIVE_VOLUME",
            f"{self._key()}: negative volume",
        )
        require(
            _ZERO <= self.taker_buy_volume <= self.volume,
            "CANDLE_TAKER_EXCEEDS",
            f"{self._key()}: taker_buy_volume outside [0, volume]",
        )
        require(
            self.trade_count >= 0, "CANDLE_NEGATIVE_TRADES", f"{self._key()}: negative trade_count"
        )

    @property
    def close_time(self) -> datetime:
        return self.open_time + self.timeframe.duration

    def _key(self) -> str:
        return f"{self.symbol} {self.timeframe.value} {self.open_time.isoformat()}"
