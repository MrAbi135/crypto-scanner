"""The shift engine resumes into the same leg as one pass (SLS v1.0.20 §3.6).

`test_structure_shift_resumes` draws short random walks, where a trend rarely
outlives one window. The current leg (v1.0.20) is the state that most needs a
long trend: its start, the last CHoCH and the candles it reads can all lie
before the window's first candle, and then only the snapshot holds them.

So these series trend: the walk drifts one way for a regime of 30-70 candles,
then turns. A close-by-close run with a window half the series' length must
leave the same facts and trend as one pass over the whole series. The series
are seeded, so a run is a verdict, not a draw.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal

import pytest
from tests.property.test_structure_shift_resumes import _facts, _service
from tests.support.strategies import _SERIES_ORIGIN

from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe

pytestmark = pytest.mark.property

_WINDOW = 80
_SIZE = 2 * _WINDOW
_SEEDS = range(24)


def _trending(seed: int) -> list[Candle]:
    rng = random.Random(seed)
    level = 1000
    drift = rng.choice((-3, 3))
    regime_left = rng.randint(30, 70)
    candles: list[Candle] = []

    for index in range(_SIZE):
        if regime_left == 0:
            drift, regime_left = -drift, rng.randint(30, 70)
        regime_left -= 1

        step = drift + rng.randint(-6, 6)
        half = rng.randint(2, 7) * (6 if rng.random() < 0.08 else 1)
        level = max(200, min(2000, level + step))
        high, low = level + half, level - half
        close = low + (2 * half * rng.randint(0, 100)) // 100
        candles.append(
            Candle(
                symbol="PROPTREND",
                timeframe=Timeframe.H1,
                open_time=_SERIES_ORIGIN + Timeframe.H1.duration * index,
                open=Decimal(level),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=Decimal(100),
                quote_volume=Decimal(10_000),
                taker_buy_volume=Decimal(50),
                trade_count=10,
                source=CandleSource.BACKFILL,
            )
        )
        level = close

    return candles


@pytest.mark.parametrize("seed", _SEEDS)
def test_a_trending_series_resumes_into_the_same_leg(seed: int) -> None:
    series = _trending(seed)

    async def both() -> tuple[list, str, list, str]:
        step = series[0].timeframe.duration
        sliding, sliding_events = _service(series)
        trend = "RANGING"

        for index in range(_WINDOW, len(series)):
            candle = series[index]
            report = await sliding.run(
                candle.symbol,
                candle.timeframe,
                candle.open_time - step * _WINDOW,
                candle.open_time + step,
            )
            trend = report.trend_state

        once, once_events = _service(series)
        whole = await once.run(
            series[0].symbol, series[0].timeframe, series[0].open_time, series[-1].open_time + step
        )

        return _facts(sliding_events), trend, _facts(once_events), whole.trend_state

    sliding, sliding_trend, once, once_trend = asyncio.run(both())

    first_window_end = series[_WINDOW].open_time.isoformat()
    once = [fact for fact in once if fact[1] >= first_window_end]
    sliding = [fact for fact in sliding if fact[1] >= first_window_end]

    assert sliding == once, (
        f"only sliding: {sorted(set(sliding) - set(once))[:4]}\n"
        f"only one pass: {sorted(set(once) - set(sliding))[:4]}"
    )
    assert sliding_trend == once_trend
