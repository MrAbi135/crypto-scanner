"""SLS v1.0.20 §3.6: the current leg's trend start and origin."""

from __future__ import annotations

from decimal import Decimal

from scanner.domain.structure.leg import (
    leg_origin,
    trend_start_fallback,
    trend_start_from_choch,
)


def _d(*values: int) -> list[Decimal]:
    return [Decimal(value) for value in values]


#             0    1    2    3    4    5    6    7    8
LOWS = _d(100, 96, 98, 94, 97, 99, 95, 98, 101)
HIGHS = _d(105, 103, 108, 104, 110, 109, 110, 107, 106)


def test_the_trend_start_from_a_turning_choch_is_the_extreme_between_swing_and_choch() -> None:
    assert trend_start_from_choch(LOWS, HIGHS, swing=1, choch=5, bullish=True) == 3
    assert trend_start_from_choch(LOWS, HIGHS, swing=1, choch=5, bullish=False) == 4


def test_the_trend_start_reads_from_the_earlier_of_swing_and_choch() -> None:
    assert trend_start_from_choch(LOWS, HIGHS, swing=5, choch=3, bullish=True) == 3


def test_ties_resolve_to_the_earliest_candle() -> None:
    # Highs 110 at 4 and at 6: the earliest wins.
    assert trend_start_from_choch(LOWS, HIGHS, swing=2, choch=7, bullish=False) == 4


def test_the_fallback_start_is_the_second_most_recent_labelled_swing() -> None:
    assert trend_start_fallback([2, 5, 7], current=8) == 5


def test_the_fallback_ignores_swings_at_or_after_the_current_candle() -> None:
    assert trend_start_fallback([2, 5, 8], current=8) == 2


def test_with_fewer_than_two_labelled_swings_the_trend_starts_now() -> None:
    assert trend_start_fallback([5], current=8) == 8


def test_the_origin_is_the_lowest_low_after_the_last_opposite_swing_before_the_extreme() -> None:
    # BULLISH from 0; running extreme over [0, 7] = high 110 at 4.
    # Opposite (swing highs) before 4: 2. Lowest low in [3, 4] = 94 at 3.
    assert leg_origin(LOWS, HIGHS, start=0, current=8, bullish=True, opposite=[2, 6]) == 3


def test_an_opposite_swing_on_the_extreme_candle_itself_is_not_before_it() -> None:
    # Swing high at 4 = the extreme: not "before" it, so the search starts after 2.
    assert leg_origin(LOWS, HIGHS, start=0, current=8, bullish=True, opposite=[2, 4]) == 3


def test_without_an_opposite_swing_the_origin_is_searched_from_the_trend_start() -> None:
    # Lowest low in [0, 4] = 94 at 3; from start 1 it is still 3; from 4 it is 4.
    assert leg_origin(LOWS, HIGHS, start=0, current=8, bullish=True, opposite=[]) == 3
    assert leg_origin(LOWS, HIGHS, start=4, current=8, bullish=True, opposite=[]) == 4


def test_an_opposite_swing_before_the_trend_start_does_not_move_the_origin_before_it() -> None:
    assert leg_origin(LOWS, HIGHS, start=4, current=8, bullish=True, opposite=[1]) == 4


def test_the_extreme_is_read_from_candles_before_the_current_one() -> None:
    # Up to candle 3 the extreme is the 108 at 2; candle 4's 110 is not seen yet.
    assert leg_origin(LOWS, HIGHS, start=0, current=4, bullish=True, opposite=[]) == 1


def test_a_trend_that_starts_on_the_current_candle_has_its_origin_there() -> None:
    assert leg_origin(LOWS, HIGHS, start=8, current=8, bullish=True, opposite=[]) == 8


def test_the_bearish_origin_mirrors_the_bullish_one() -> None:
    # BEARISH from 0; running extreme over [0, 7] = low 94 at 3. Opposite
    # (swing lows) before 3: 1. Highest high in [2, 3] = 108 at 2.
    assert leg_origin(LOWS, HIGHS, start=0, current=8, bullish=False, opposite=[1, 5]) == 2
