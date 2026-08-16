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


def candle_series(
    *,
    min_size: int = 1,
    max_size: int = 80,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPUSDT",
) -> st.SearchStrategy[list[Candle]]:
    """Contiguous, always-valid candle series for property tests.

    Each candle is generated from a midpoint and a half-range, with open and
    close pinned to the midpoint. That construction satisfies the §2.15
    intrinsic checks by shape rather than by filtering, so hypothesis spends
    its budget exploring price *structure* instead of rediscovering that
    `high >= max(open, close)`.
    """

    def build(pairs: list[tuple[int, int]]) -> list[Candle]:
        return [
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=_SERIES_ORIGIN + timeframe.duration * index,
                open=Decimal(mid),
                high=Decimal(mid + half),
                low=Decimal(mid - half),
                close=Decimal(mid),
                volume=Decimal(100),
                quote_volume=Decimal(10_000),
                taker_buy_volume=Decimal(50),
                trade_count=10,
                source=CandleSource.BACKFILL,
            )
            for index, (mid, half) in enumerate(pairs)
        ]

    return st.lists(
        st.tuples(
            st.integers(min_value=50, max_value=500),
            st.integers(min_value=0, max_value=20),
        ),
        min_size=min_size,
        max_size=max_size,
    ).map(build)
