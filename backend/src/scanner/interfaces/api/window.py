"""Where a read window ends (Sprint S10a).

Anchored to the context's own newest candle, not to the wall clock.

The first version used `clock.now()`. For a live symbol the two are the same,
so it looked right -- and every historical context returned nothing. Loading
the golden datasets exposed it: their candles are dated January, so the chart
asked for the last five hundred hours of *today* and got an empty series, then
correctly reported "no candles for this context yet" for twelve datasets that
were sitting in the table.

A symbol that stopped trading, a delisted pair, or any window an operator wants
to inspect fails the same way, and the failure is a plausible-looking empty
chart rather than an error.
"""

from __future__ import annotations

from datetime import datetime

from scanner.application.ports import CandleRepository, Clock
from scanner.shared import Timeframe


async def window_end(
    candles: CandleRepository,
    symbol: str,
    timeframe: Timeframe,
    clock: Clock,
) -> datetime:
    """One step past the newest stored candle, or now if the context is empty.

    Exclusive, so the newest candle falls inside the window rather than on its
    boundary. Falling back to `now` keeps an unknown symbol behaving as before
    instead of failing.
    """
    latest = await candles.latest_open_time(symbol, timeframe)

    if latest is None:
        return clock.now()

    return latest + timeframe.duration
