"""Tests for Wilder ATR and the derived-precision rule (SLS §2, §0.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.common import (
    ATR_PERIOD,
    DERIVED_DP,
    Candle,
    CandleSource,
    quantise_derived,
    true_range,
    wilder_atr,
)
from scanner.shared import Timeframe

T0 = datetime(2026, 1, 5, tzinfo=UTC)


def candle(index: int, *, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="ATRTEST",
        timeframe=Timeframe.H1,
        open_time=T0 + timedelta(hours=index),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def flat_series(count: int, *, height: str = "2") -> list[Candle]:
    """Identical candles, so every true range equals `height`."""

    span = Decimal(height)
    return [
        candle(index, high=str(Decimal("100") + span), low="100", close="100")
        for index in range(count)
    ]


def test_period_matches_the_specification() -> None:
    assert ATR_PERIOD == 14
    assert DERIVED_DP == 4


def test_first_candle_true_range_degrades_to_high_minus_low() -> None:
    """No previous close exists, so the other two terms are undefined."""

    series = [candle(0, high="105", low="100", close="102")]

    assert true_range(series, 0) == Decimal("5")


def test_true_range_uses_the_previous_close_when_it_gaps() -> None:
    """A gap makes the close-relative terms dominate high-low (SLS §2)."""

    series = [
        candle(0, high="105", low="100", close="100"),
        candle(1, high="120", low="118", close="119"),
    ]

    # high-low is 2, but the gap from the prior close of 100 is 20.
    assert true_range(series, 1) == Decimal("20")


@pytest.mark.parametrize("index", [0, 5, 12])
def test_atr_is_undefined_while_seeding(index: int) -> None:
    """None, not zero.

    Zero would be indistinguishable from a motionless market, and a caller
    dividing by it would get an answer instead of an error.
    """

    assert wilder_atr(flat_series(20), index) is None


def test_atr_is_defined_from_the_fourteenth_candle() -> None:
    assert wilder_atr(flat_series(20), ATR_PERIOD - 1) is not None


def test_constant_true_range_converges_to_that_constant() -> None:
    """The recursion's fixed point: (c x 13 + c) / 14 = c.

    This is what lets golden datasets declare long flat history and still
    land on an exact ATR.
    """

    series = flat_series(100, height="3")

    assert wilder_atr(series, 99) == Decimal("3")


def test_wilder_smooths_rather_than_averaging_the_window() -> None:
    """The distinction the whole correction was about.

    Wilder weights history far more heavily than a rolling window does, so
    after a volatility spike it sits *below* the simple mean the code used
    before. Comparing against the mean directly is the honest test — an
    arbitrary constant would only assert that some number is some size.
    """

    series = flat_series(40, height="3")
    series.append(candle(40, high="120", low="103", close="103"))
    series.append(candle(41, high="120", low="103", close="103"))

    atr = wilder_atr(series, 41)

    simple_mean = sum(
        (true_range(series, index) for index in range(42 - ATR_PERIOD, 42)),
        Decimal(0),
    ) / Decimal(ATR_PERIOD)

    assert atr is not None
    assert atr < simple_mean, "Wilder must lag a rolling mean after a spike"
    assert atr > Decimal("3"), "but it must still have responded to the spike"


def test_out_of_range_index_is_an_error_not_a_silent_zero() -> None:
    with pytest.raises(IndexError, match="candle index out of range"):
        wilder_atr(flat_series(20), 99)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.9854242054724053640232107909", "0.9854"),
        ("0.1522187086201024111673585957", "0.1522"),
        ("1", "1.0000"),
        ("2.00005", "2.0000"),  # ROUND_HALF_EVEN breaks the tie downward
        ("2.00015", "2.0002"),  # ...and upward, to the even digit
    ],
)
def test_derived_values_quantise_to_four_places(raw: str, expected: str) -> None:
    assert str(quantise_derived(Decimal(raw))) == expected


def test_quantisation_never_feeds_a_comparison() -> None:
    """§0.4: presentation rule, never a decision rule.

    Two values that differ below the fourth place record identically, which
    is precisely why the unquantised value must be what thresholds see.
    """

    a = Decimal("0.98544")
    b = Decimal("0.98543")

    assert quantise_derived(a) == quantise_derived(b)
    assert a != b
