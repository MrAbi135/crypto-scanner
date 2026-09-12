"""Canonical hypothesis strategies (S0.3 §3).

The generators every future engine/golden test reuses — decimals-as-strings,
timeframes, ULIDs, UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import strategies as st

from scanner.domain.common import Candle, CandleSource
from scanner.shared.ids import new_ulid
from scanner.shared.timeutil import Timeframe


def decimal_strings(min_value: str = "-1e15", max_value: str = "1e15") -> st.SearchStrategy[str]:
    return st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
    ).map(str)


def timeframes() -> st.SearchStrategy[Timeframe]:
    return st.sampled_from(list(Timeframe))


def epoch_millis() -> st.SearchStrategy[int]:
    return st.integers(min_value=0, max_value=(1 << 48) - 1)


def ulids() -> st.SearchStrategy[str]:
    return epoch_millis().map(lambda ms: new_ulid(timestamp_ms=ms))


def utc_datetimes() -> st.SearchStrategy[object]:
    return st.datetimes(timezones=st.just(UTC))


_SERIES_ORIGIN = datetime(2026, 1, 5, tzinfo=UTC)  # Monday — valid for every TF


def walking_candle_series(
    *,
    min_size: int = 20,
    max_size: int = 120,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPWALK",
) -> st.SearchStrategy[list[Candle]]:
    """A price series whose level *walks* instead of teleporting.

    `candle_series` draws each midpoint independently over a wide band, which
    is ideal for structure -- §3.1 reads highs and lows and does not care how
    price got there. It is useless for anything denominated in ATR. A jump
    between two independent midpoints becomes the candle's true range through
    the gap terms, so ATR runs an order of magnitude above any single candle's
    own range, and every `range >= n x ATR` test fails by construction.

    Measured: of 200 series from `candle_series`, **zero** contained a single
    §5.10 displacement. A property over that strategy is not lenient, it is
    vacuous -- it passes by finding nothing, which is the failure mode this
    project keeps naming.

    So this walks: each candle steps a small amount from the last close, and
    a drawn subset of candles carries a wide range and a close pinned near one
    extreme -- the shape §5.10 calls displacement. The impulses are drawn, not
    planted at fixed offsets, so hypothesis still explores where they land and
    how large they are.
    """

    def build(rows: list[tuple[int, int, int, int, bool]]) -> list[Candle]:
        candles: list[Candle] = []
        level = 1000

        for index, (step, spread, body_lo, body_hi, impulse) in enumerate(rows):
            level = max(200, min(2000, level + step))

            half = spread * 8 if impulse else spread
            high = level + half
            low = level - half

            if impulse and half > 0:
                # Close inside the top or bottom eighth: §5.10 wants the close
                # in the extreme 25% of the range, and a full body behind it.
                if body_lo % 2:
                    open_price, close_price = low, high - half // 8
                else:
                    open_price, close_price = high, low + half // 8
            elif half > 0:
                span = 2 * half
                open_price = low + (span * (body_lo % 101)) // 100
                close_price = low + (span * (body_hi % 101)) // 100
            else:
                open_price = close_price = level

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=_SERIES_ORIGIN + timeframe.duration * index,
                    open=Decimal(open_price),
                    high=Decimal(high),
                    low=Decimal(low),
                    close=Decimal(close_price),
                    volume=Decimal(100),
                    quote_volume=Decimal(10_000),
                    taker_buy_volume=Decimal(50),
                    trade_count=10,
                    source=CandleSource.BACKFILL,
                )
            )

            level = close_price

        return candles

    return st.lists(
        st.tuples(
            st.integers(min_value=-6, max_value=6),
            st.integers(min_value=1, max_value=6),
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
            st.booleans(),
        ),
        min_size=min_size,
        max_size=max_size,
    ).map(build)


def candle_series(
    *,
    min_size: int = 1,
    max_size: int = 80,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPUSDT",
    with_bodies: bool = False,
) -> st.SearchStrategy[list[Candle]]:
    """Contiguous, always-valid candle series for property tests.

    Each candle is generated from a midpoint and a half-range. That
    construction satisfies the §2.15 intrinsic checks by shape rather than by
    filtering, so hypothesis spends its budget exploring price *structure*
    instead of rediscovering that `high >= max(open, close)`.

    `with_bodies` opens and closes the candle inside its own range instead of
    pinning both to the midpoint. The default stays body-less because that is
    what the swing properties want -- §3.1 reads highs and lows and nothing
    else. §5.10 does read the body, and `body == 0` makes every candle fail
    its first condition, so a displacement property over the default strategy
    would pass without ever detecting one.
    """

    def build(rows: list[tuple[int, int, int, int]]) -> list[Candle]:
        candles = []

        for index, (mid, half, open_offset, close_offset) in enumerate(rows):
            high = mid + half
            low = mid - half

            if with_bodies and half > 0:
                # Offsets are drawn 0..100 and scaled into the candle's own
                # range, so the OHLC ordering holds by construction for any
                # half-range hypothesis picks.
                open_price = low + (2 * half * open_offset) // 100
                close_price = low + (2 * half * close_offset) // 100
            else:
                open_price = close_price = mid

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=_SERIES_ORIGIN + timeframe.duration * index,
                    open=Decimal(open_price),
                    high=Decimal(high),
                    low=Decimal(low),
                    close=Decimal(close_price),
                    volume=Decimal(100),
                    quote_volume=Decimal(10_000),
                    taker_buy_volume=Decimal(50),
                    trade_count=10,
                    source=CandleSource.BACKFILL,
                )
            )

        return candles

    return st.lists(
        st.tuples(
            st.integers(min_value=50, max_value=500),
            st.integers(min_value=0, max_value=20),
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
        ),
        min_size=min_size,
        max_size=max_size,
    ).map(build)
