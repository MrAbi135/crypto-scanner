"""Is a stored series covered, or has it stopped? (§2.12's readiness half.)

Pure, and shared, because two callers ask it: the ingest process's readiness
probe and §18.3's status row. Re-derived in each, the two would eventually
disagree about what `BEHIND` means -- and the disagreement would surface as a
dashboard calling a feed healthy while the probe held the process unready, or
the reverse.

**This is not §2.12's feed freshness.** That table is in seconds of stream lag
and is measured where the stream arrives; nothing here can see it. This answers
the narrower question the stored candles can answer: has the next close
happened yet, and if it should have, did it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from scanner.shared import Timeframe


class Coverage(str, Enum):
    """Nothing has ever arrived for this series."""

    NO_DATA = "NO_DATA"

    # Covered, and the next close has not happened yet. Normal for every
    # timeframe, all the time -- the state a healthy series is in between
    # closes, and the reason a bare "is it current" check reports every slow
    # timeframe as broken for most of its life.
    AWAITING_CLOSE = "AWAITING_CLOSE"

    # Covered, but a close that should have arrived has not. Distinguished
    # from NO_DATA because "nothing ever arrived" and "arrivals stopped" call
    # for different investigations.
    BEHIND = "BEHIND"


def coverage_of(
    latest_open_time: datetime | None,
    timeframe: Timeframe,
    now: datetime,
) -> Coverage:
    """Classify one series from its newest stored candle.

    `latest_open_time` is an *open* time, so the candle it names closed one
    interval later. One further interval of slack is the window in which the
    next close has not happened yet.
    """
    if latest_open_time is None:
        return Coverage.NO_DATA

    closed_at = latest_open_time + timeframe.duration

    if now - closed_at <= timeframe.duration:
        return Coverage.AWAITING_CLOSE

    return Coverage.BEHIND


def candles_behind(
    latest_open_time: datetime | None,
    timeframe: Timeframe,
    now: datetime,
) -> int:
    """How many closes are missing, for a reader who wants a number.

    Zero while awaiting a close: a series that is merely between closes is not
    behind by one. Reported alongside the state rather than instead of it --
    "BEHIND by 3" and "BEHIND by 300" are the same state and not the same
    problem.
    """
    if latest_open_time is None:
        return 0

    elapsed = now - (latest_open_time + timeframe.duration)

    if elapsed <= timeframe.duration:
        return 0

    return int(elapsed // timeframe.duration)
