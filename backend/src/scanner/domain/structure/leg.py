"""SLS v1.0.20 §3.6: the current leg a CHoCH may break inside.

Pure functions over one candle history addressed by position (0 = oldest
candle the caller holds). The caller maps positions to candle times; nothing
here keeps state.

In BULLISH (mirror in BEARISH):

* **trend start** -- the low of the move that turned the trend bullish: the
  lowest low from the swing the turning CHoCH broke up to that CHoCH's
  candle; without a recorded turning CHoCH, the second most recent confirmed
  external HL;
* **running extreme** -- the highest high from the trend start to the
  previous candle;
* **leg origin** -- the lowest low between the last confirmed external swing
  high before the running extreme and the running extreme itself (from the
  trend start when there is none after it).

Ties resolve to the earliest candle.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def _lowest(lows: Sequence[Decimal], first: int, last: int) -> int:
    """Position of the lowest low in [first, last], the earliest on ties."""
    return min(range(first, last + 1), key=lambda index: lows[index])


def _highest(highs: Sequence[Decimal], first: int, last: int) -> int:
    """Position of the highest high in [first, last], the earliest on ties."""
    return max(range(first, last + 1), key=lambda index: highs[index])


def trend_start_from_choch(
    lows: Sequence[Decimal],
    highs: Sequence[Decimal],
    *,
    swing: int,
    choch: int,
    bullish: bool,
) -> int:
    """The extreme of the move that turned the trend: from the broken swing to
    the turning CHoCH's candle, the lowest low (BULLISH) or highest high."""
    first = min(swing, choch)
    return _lowest(lows, first, choch) if bullish else _highest(highs, first, choch)


def trend_start_fallback(labelled: Sequence[int], current: int) -> int:
    """Without a turning CHoCH: the second most recent confirmed external HL
    (BULLISH) or LH before `current`, given their positions. With fewer than
    two, the trend starts at `current` itself."""
    before = sorted(position for position in labelled if position < current)
    return before[-2] if len(before) >= 2 else current


def leg_origin(
    lows: Sequence[Decimal],
    highs: Sequence[Decimal],
    *,
    start: int,
    current: int,
    bullish: bool,
    opposite: Sequence[int],
) -> int:
    """The first position of the current leg, evaluated for the candle at
    `current` from candles before it.

    `opposite` holds the positions of the confirmed external swings of the
    opposite kind (swing highs in BULLISH). Swings at or after the returned
    position are the ones a CHoCH may break.
    """
    if current - 1 < start:
        return start

    extreme = _highest(highs, start, current - 1) if bullish else _lowest(lows, start, current - 1)
    before = [position for position in opposite if position < extreme]
    first = max((max(before) if before else start - 1) + 1, start)

    if first > extreme:
        return start

    return _lowest(lows, first, extreme) if bullish else _highest(highs, first, extreme)
