"""Canonical hypothesis strategies (S0.3 §3).

The generators every future engine/golden test reuses — decimals-as-strings,
timeframes, ULIDs, UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from hypothesis import strategies as st

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
