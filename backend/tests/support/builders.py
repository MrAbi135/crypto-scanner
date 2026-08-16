"""Candle/series builders composing shared primitives."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from scanner.domain.common import DETECTION_MIN_CANDLES, Candle, CandleSource
from scanner.shared import Timeframe, dec

BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)  # Monday — valid for all TFs incl. W1


def make_candle(
    *,
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.H1,
    open_time: datetime | None = None,
    open_: Decimal | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    close: Decimal | None = None,
    volume: Decimal | None = None,
    taker_buy_volume: Decimal | None = None,
) -> Candle:
    o = open_ if open_ is not None else dec("100")
    c = close if close is not None else dec("101")
    h = high if high is not None else max(o, c) + dec("1")
    lo = low if low is not None else min(o, c) - dec("1")
    v = volume if volume is not None else dec("50")
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time or BASE_TIME,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        quote_volume=v * o,
        taker_buy_volume=taker_buy_volume if taker_buy_volume is not None else v / 2,
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def pad_for_warmup(candles: list[Candle]) -> list[Candle]:
    """Prepend flat history so a fixture clears the SLS §1.9 detection floor.

    The engines refuse to analyse fewer than `DETECTION_MIN_CANDLES` closed
    candles, so a hand-built fixture of a dozen candles now detects nothing at
    all. Padding is the honest fix: lowering the floor for tests would exercise
    a configuration production never runs.

    The padding copies the first candle, which makes the seam a flat window —
    rejected by §3.1's "at least one left-hand candle materially lower" guard,
    so the padding cannot manufacture a swing the fixture did not intend. This
    mirrors the `filler` mechanism the golden datasets use.
    """

    if not candles:
        return candles

    first = candles[0]
    step = first.timeframe.duration
    needed = max(0, DETECTION_MIN_CANDLES - len(candles))

    padding = [
        replace(first, open_time=first.open_time - step * (needed - offset))
        for offset in range(needed)
    ]

    return padding + candles


def make_series(
    count: int, *, timeframe: Timeframe = Timeframe.H1, start: datetime | None = None
) -> list[Candle]:
    begin = start or BASE_TIME
    return [
        make_candle(timeframe=timeframe, open_time=begin + timeframe.duration * i)
        for i in range(count)
    ]
