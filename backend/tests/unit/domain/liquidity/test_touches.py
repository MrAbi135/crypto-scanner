"""§4.2's `touches` component (SLS §4.2).

Every case here is one of the three words in §4.2's sentence: *separate*
approaches that *reversed* without *breaching*. Each of the three, dropped,
produces a plausible number -- which is why the component sat at a hardcoded
zero for as long as it did without anything looking wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.liquidity import LiquiditySide, count_pool_touches
from scanner.shared import Timeframe

T0 = datetime(2026, 8, 15, tzinfo=UTC)
EPSILON = Decimal("0.5")


def candle(index: int, *, high: str, low: str) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=T0 + timedelta(hours=index),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(low),
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        taker_buy_volume=Decimal("1"),
        trade_count=1,
        source=CandleSource.STREAM,
    )


def bsl(candles: list[Candle], *, level: str = "100") -> int:
    return count_pool_touches(
        candles,
        side=LiquiditySide.BSL,
        band_low=Decimal(level),
        band_high=Decimal(level),
        epsilon=EPSILON,
    )


def ssl(candles: list[Candle], *, level: str = "100") -> int:
    return count_pool_touches(
        candles,
        side=LiquiditySide.SSL,
        band_low=Decimal(level),
        band_high=Decimal(level),
        epsilon=EPSILON,
    )


# `away` never reaches the level; `at` reaches it and turns; `through` goes
# beyond it by more than ε.
AWAY = {"high": "95", "low": "90"}
AT = {"high": "100", "low": "95"}
THROUGH = {"high": "102", "low": "95"}


def series(*shapes: dict[str, str]) -> list[Candle]:
    return [candle(i, **shape) for i, shape in enumerate(shapes)]


def test_a_market_that_never_came_back_has_no_touches() -> None:
    assert bsl(series(AWAY, AWAY, AWAY)) == 0


def test_one_approach_that_turned_is_one_touch() -> None:
    assert bsl(series(AWAY, AT, AWAY)) == 1


def test_two_approaches_separated_by_a_departure_are_two() -> None:
    assert bsl(series(AWAY, AT, AWAY, AT, AWAY)) == 2


def test_five_candles_loitering_on_the_level_are_one_approach() -> None:
    """*Separate*, in §4.2's sentence.

    Without it the component measures how long price sat on a level rather than
    how many times it was rejected there, and a slow sideways drift scores the
    maximum -- which is the opposite of what an engineered-liquidity score is
    supposed to reward.
    """
    assert bsl(series(AWAY, AT, AT, AT, AT, AT, AWAY)) == 1


def test_a_candle_that_went_through_is_not_a_touch() -> None:
    """*Without breaching*. A candle past the level is §4.6's sweep or §4.2's
    break, and paying the pool for the event that ends it would be perverse."""

    assert bsl(series(AWAY, THROUGH, AWAY)) == 0


def test_one_breach_disqualifies_the_whole_approach() -> None:
    """A poke through followed by a candle that merely reaches is one approach,
    and it went through. Resetting on the second candle would let any sweep be
    laundered into a touch by the candle after it."""

    assert bsl(series(AWAY, THROUGH, AT, AWAY)) == 0


def test_a_breach_does_not_poison_a_later_separate_approach() -> None:
    assert bsl(series(AWAY, THROUGH, AWAY, AT, AWAY)) == 1


def test_an_approach_still_in_progress_is_not_counted() -> None:
    """*Reversed*, past tense. The series ends with price still at the level;
    the next candle may take it out, and a score that counted this would fall
    when that happened."""

    assert bsl(series(AWAY, AT, AT)) == 0


def test_it_stops_counting_at_three() -> None:
    # §4.2 scores `min(touches, 3)`, so a fourth changes no answer -- and the
    # cap bounds what a long window can do to the count.
    assert bsl(series(AWAY, AT, AWAY, AT, AWAY, AT, AWAY, AT, AWAY)) == 3


def test_epsilon_is_a_reach_not_a_requirement_to_arrive() -> None:
    """§4.2 says approaches that reversed *within ε*. A candle half a tick
    short of the level was still rejected by it."""

    near = {"high": "99.6", "low": "95"}

    assert bsl(series(AWAY, near, AWAY)) == 1


def test_epsilon_also_forgives_a_marginal_overshoot() -> None:
    # Within ε past the level is still the level, not a break of it -- the same
    # tolerance §4 applies everywhere else.
    marginal = {"high": "100.4", "low": "95"}

    assert bsl(series(AWAY, marginal, AWAY)) == 1


def test_the_sell_side_is_the_mirror_and_not_a_copy() -> None:
    """The direction is easy to get right in one branch and wrong in the other,
    and a sell-side pool that counted highs would score every candle."""

    below_away = {"high": "110", "low": "105"}
    below_at = {"high": "110", "low": "100"}
    below_through = {"high": "110", "low": "98"}

    assert ssl(series(below_away, below_at, below_away)) == 1
    assert ssl(series(below_away, below_through, below_away)) == 0


def test_a_band_counts_a_turn_anywhere_inside_it() -> None:
    """A cluster pool's stops sit across the band, which is why §4.2 keeps it
    "for sweep tolerance". A candle that entered the band and turned was
    rejected by the pool even though it never reached the far edge."""

    inside = {"high": "100.5", "low": "95"}

    counted = count_pool_touches(
        series(AWAY, inside, AWAY),
        side=LiquiditySide.BSL,
        band_low=Decimal("100"),
        band_high=Decimal("101"),
        epsilon=EPSILON,
    )

    assert counted == 1


def test_a_band_is_breached_only_past_its_far_edge() -> None:
    through_band = {"high": "101.6", "low": "95"}

    counted = count_pool_touches(
        series(AWAY, through_band, AWAY),
        side=LiquiditySide.BSL,
        band_low=Decimal("100"),
        band_high=Decimal("101"),
        epsilon=EPSILON,
    )

    assert counted == 0


def test_deep_inside_the_band_is_still_not_a_breach() -> None:
    """The case that separates the far edge from the near one.

    A candle past the band's *low* but short of its *high* is inside the pool,
    not through it. Measuring the breach at the near edge instead reads every
    real entry into a cluster band as a sweep -- which passes both of the tests
    above, because they only probe outside the band and well past it.
    """
    deep = {"high": "101.2", "low": "95"}

    counted = count_pool_touches(
        series(AWAY, deep, AWAY),
        side=LiquiditySide.BSL,
        band_low=Decimal("100"),
        band_high=Decimal("101"),
        epsilon=EPSILON,
    )

    assert counted == 1


def test_deep_inside_a_sell_side_band_is_not_a_breach_either() -> None:
    below_away = {"high": "110", "low": "105"}
    deep = {"high": "110", "low": "99.8"}

    counted = count_pool_touches(
        [candle(0, **below_away), candle(1, **deep), candle(2, **below_away)],
        side=LiquiditySide.SSL,
        band_low=Decimal("99"),
        band_high=Decimal("100"),
        epsilon=EPSILON,
    )

    assert counted == 1


def test_no_candles_is_zero_rather_than_an_error() -> None:
    # The ordinary state of a pool on the candle it was confirmed.
    assert bsl([]) == 0


def test_it_refuses_arguments_that_cannot_mean_anything() -> None:
    with pytest.raises(ValueError):
        count_pool_touches(
            [],
            side=LiquiditySide.BSL,
            band_low=Decimal("100"),
            band_high=Decimal("100"),
            epsilon=Decimal("-1"),
        )

    with pytest.raises(ValueError):
        count_pool_touches(
            [],
            side=LiquiditySide.BSL,
            band_low=Decimal("101"),
            band_high=Decimal("100"),
            epsilon=EPSILON,
        )
