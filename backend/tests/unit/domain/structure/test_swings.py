"""Tests for the shared S4 swing engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.structure import (
    SwingKind,
    SwingStrength,
    detect_external_swings,
    detect_internal_swings,
    detect_swings,
    swing_window,
)
from scanner.shared import Timeframe


def candle(
    index: int,
    *,
    high: str,
    low: str,
) -> Candle:
    high_value = Decimal(high)
    low_value = Decimal(low)
    midpoint = (high_value + low_value) / Decimal("2")

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        )
        + timedelta(hours=index),
        open=midpoint,
        high=high_value,
        low=low_value,
        close=midpoint,
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def test_internal_and_external_windows_match_spec() -> None:
    assert swing_window(SwingStrength.INTERNAL) == 2

    assert swing_window(SwingStrength.EXTERNAL) == 5


def test_internal_swing_high_confirms_after_two_right_candles() -> None:
    candles = [
        candle(0, high="10", low="5"),
        candle(1, high="11", low="6"),
        candle(2, high="15", low="7"),
        candle(3, high="12", low="6"),
        candle(4, high="11", low="5"),
    ]

    swings = detect_internal_swings(candles)

    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]

    assert len(highs) == 1
    assert highs[0].index == 2
    assert highs[0].price == Decimal("15")


def test_internal_swing_low_confirms_after_two_right_candles() -> None:
    candles = [
        candle(0, high="15", low="10"),
        candle(1, high="14", low="9"),
        candle(2, high="13", low="5"),
        candle(3, high="14", low="8"),
        candle(4, high="15", low="9"),
    ]

    swings = detect_internal_swings(candles)

    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]

    assert len(lows) == 1
    assert lows[0].index == 2
    assert lows[0].price == Decimal("5")


def test_equal_high_confirms_last_member_of_equal_set() -> None:
    candles = [
        candle(0, high="10", low="5"),
        candle(1, high="12", low="6"),
        candle(2, high="15", low="7"),
        candle(3, high="15", low="8"),
        candle(4, high="13", low="7"),
        candle(5, high="12", low="6"),
    ]

    swings = detect_internal_swings(candles)

    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]

    assert [swing.index for swing in highs] == [3]


def test_equal_low_confirms_last_member_of_equal_set() -> None:
    candles = [
        candle(0, high="15", low="10"),
        candle(1, high="14", low="8"),
        candle(2, high="13", low="5"),
        candle(3, high="14", low="5"),
        candle(4, high="15", low="7"),
        candle(5, high="16", low="8"),
    ]

    swings = detect_internal_swings(candles)

    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]

    assert [swing.index for swing in lows] == [3]


def test_unconfirmed_right_edge_never_emits_swing() -> None:
    candles = [
        candle(0, high="10", low="5"),
        candle(1, high="11", low="6"),
        candle(2, high="12", low="7"),
        candle(3, high="20", low="8"),
    ]

    assert detect_internal_swings(candles) == ()


def test_external_swing_requires_five_candles_each_side() -> None:
    candles = [
        candle(
            index,
            high=str(20 - abs(5 - index)),
            low=str(5 + abs(5 - index)),
        )
        for index in range(11)
    ]

    swings = detect_external_swings(candles)

    assert any(swing.index == 5 and swing.kind is SwingKind.HIGH for swing in swings)


def test_negative_epsilon_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="epsilon must be non-negative",
    ):
        detect_swings(
            [],
            strength=SwingStrength.INTERNAL,
            epsilon=Decimal("-0.1"),
        )


def test_append_only_detection_does_not_repaint_confirmed_swing() -> None:
    initial = [
        candle(0, high="10", low="5"),
        candle(1, high="12", low="6"),
        candle(2, high="18", low="7"),
        candle(3, high="14", low="6"),
        candle(4, high="13", low="5"),
    ]

    before = detect_internal_swings(initial)

    extended = [
        *initial,
        candle(5, high="30", low="10"),
        candle(6, high="20", low="9"),
    ]

    after = detect_internal_swings(extended)

    confirmed_before = [
        swing for swing in before if swing.index == 2 and swing.kind is SwingKind.HIGH
    ]

    confirmed_after = [
        swing for swing in after if swing.index == 2 and swing.kind is SwingKind.HIGH
    ]

    assert confirmed_before == confirmed_after


def test_an_equal_run_filling_the_left_window_is_still_a_swing() -> None:
    """§3.1: "every external swing is by construction also an internal one."

    Found by `test_every_external_swing_is_also_an_internal_swing` in the
    property suite, on twelve candles with a three-candle equal run.

    The left-side check used to read "some candle inside the k window is
    materially lower", and an equal run can fill that window. Highs of 50 with
    51 at indices 4, 5 and 6: at `k_ext = 5` the left window reaches back to
    index 1 and finds a 50, so index 6 confirmed as an external swing; at
    `k_int = 2` the window is only indices 4 and 5, both 51, so the same pivot
    did not exist internally. One swing, visible at one strength and not the
    other, which is exactly what §3.1 says cannot happen.

    The run is one extreme, so the check now looks past it to the candle
    before the set. A run reaching the start of the series still has nothing
    lower to its left, which is how a flat shelf is refused -- and no golden
    dataset changed, all of which begin with flat filler.
    """
    highs = ["50", "50", "50", "50", "51", "51", "51", "50", "50", "50", "50", "50"]
    candles = [candle(i, high=high, low="49") for i, high in enumerate(highs)]

    internal = detect_swings(candles, strength=SwingStrength.INTERNAL)
    external = detect_swings(candles, strength=SwingStrength.EXTERNAL)

    assert [(s.index, s.kind) for s in external] == [(6, SwingKind.HIGH)]

    # The invariant itself: the external pivot is among the internal ones.
    assert (6, SwingKind.HIGH) in [(s.index, s.kind) for s in internal]


def test_a_flat_pause_in_a_descent_is_not_a_swing_high() -> None:
    """§3.1's left-hand guard, taken literally: the shelf must rise above
    what precedes it.

    Highs: 90, then the 105 peak, then a three-candle pause at 100 that
    fills the whole `k_int = 2` left window, then two lower rights. The
    walk-back used to consume the 105 as if it were part of the equal set
    (`>= candidate - epsilon` is true of higher candles too) and kept
    going until it found the 90 -- so the pause on the way down from 105
    minted a swing high, and on real data something lower always exists
    further back.
    """
    highs = ["90", "105", "100", "100", "100", "98", "97"]
    candles = [candle(i, high=high, low="80") for i, high in enumerate(highs)]

    swings = detect_internal_swings(candles)

    assert [(s.index, s.kind) for s in swings if s.kind is SwingKind.HIGH] == []


def test_a_flat_pause_in_an_ascent_is_not_a_swing_low() -> None:
    """The mirror: a shelf on the way up from a lower low is not a trough."""

    lows = ["110", "95", "100", "100", "100", "102", "103"]
    candles = [candle(i, high="120", low=low) for i, low in enumerate(lows)]

    swings = detect_internal_swings(candles)

    assert [(s.index, s.kind) for s in swings if s.kind is SwingKind.LOW] == []


def test_a_shelf_that_rises_above_its_left_is_still_a_swing() -> None:
    """The positive control for the guard: the same shape with the candle
    before the equal run materially LOWER confirms, so the fix cannot have
    thrown the legitimate case out with the false one."""

    highs = ["90", "95", "100", "100", "100", "98", "97"]
    candles = [candle(i, high=high, low="80") for i, high in enumerate(highs)]

    swings = detect_internal_swings(candles)

    assert [(s.index, s.kind) for s in swings if s.kind is SwingKind.HIGH] == [(4, SwingKind.HIGH)]
