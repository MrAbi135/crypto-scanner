"""Momentum score against SLS §7.1. Expectations derived from the spec."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tests.support.builders import make_candle

from scanner.domain.momentum import (
    WARMUP_CANDLES,
    MomentumDirection,
    directional_roc,
    momentum_score,
)
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def candle(index: int, *, open_: str, close: str, high: str, low: str, volume: str = "10"):
    return make_candle(
        timeframe=Timeframe.H4,
        open_time=BASE + Timeframe.H4.duration * index,
        open_=Decimal(open_),
        close=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        volume=Decimal(volume),
    )


def flat(count: int, *, price: str = "100"):
    """Doji-ish history: tiny range, no body, so it contributes no direction."""
    return [
        candle(
            i, open_=price, close=price, high=str(Decimal(price) + 1), low=str(Decimal(price) - 1)
        )
        for i in range(count)
    ]


def trending(count: int, *, step: int = 5, start: int = 100, volume: str = "10"):
    """A clean staircase up: every candle closes above its open.

    Wick padding scales with `step`, so changing the step changes the price
    scale without changing the *shape*. Fixed padding does not: at step 5 a
    1-unit wick is 20% of the body and at step 50 it is 2%, which makes two
    series that look identical on a chart genuinely different candles -- and
    then an ATR-normalisation test compares two things that were never the
    same. That mistake failed this file once before it was corrected here.
    """
    series = []
    pad = step // 5

    for i in range(count):
        low = start + step * i
        series.append(
            candle(
                i,
                open_=str(low),
                close=str(low + step),
                high=str(low + step + pad),
                low=str(low - pad),
                volume=volume,
            )
        )

    return series


class TestWarmup:
    def test_no_score_before_thirty_candles(self) -> None:
        """§7.1 requires a 30-candle warm-up.

        None rather than a low number: an unwarmed context has no reading, and
        a small score is indistinguishable from genuinely flat energy.
        """
        series = trending(WARMUP_CANDLES - 1)

        assert momentum_score(series, len(series) - 1) is None

    def test_a_score_appears_once_warm(self) -> None:
        series = trending(WARMUP_CANDLES)

        assert momentum_score(series, WARMUP_CANDLES - 1) is not None


class TestDirectionalRoc:
    def test_it_is_atr_normalised_so_scale_does_not_change_it(self) -> None:
        """§7 opens by saying every measure is ATR-normalised for comparability.

        Two staircases with identical shape but ten times the step must read the
        same, or a high-priced symbol would always look more energetic.
        """
        small = trending(40, step=5, start=100)
        large = trending(40, step=50, start=1000)

        near = directional_roc(small, 39)
        far = directional_roc(large, 39)

        assert near is not None and far is not None
        assert abs(near - far) < Decimal("0.01")

    def test_it_is_signed_with_direction(self) -> None:
        up = trending(40)

        roc = directional_roc(up, 39)

        assert roc is not None and roc > 0


class TestComponents:
    def test_the_score_is_the_sum_of_its_four_parts(self) -> None:
        """§7.1 calls the score auditable; a total that is not its parts is not.

        Checked on an uncapped reading, since the neutral cap deliberately
        breaks the identity.
        """
        result = momentum_score(trending(40), 39)

        assert result is not None
        assert result.neutral_capped is False

        assert result.score == (
            result.roc_component
            + result.consistency_component
            + result.body_component
            + result.participation_component
        )

    def test_a_clean_trend_scores_near_the_ceiling(self) -> None:
        result = momentum_score(trending(40), 39)

        assert result is not None
        assert result.direction is MomentumDirection.UP
        assert result.score > Decimal(75)

    def test_every_component_stays_inside_its_band(self) -> None:
        result = momentum_score(trending(40), 39)

        assert result is not None

        for component in (
            result.roc_component,
            result.consistency_component,
            result.body_component,
            result.participation_component,
        ):
            assert Decimal(0) <= component <= Decimal(25)


class TestNeutralEdgeCase:
    def test_a_directionless_window_is_capped_and_carries_no_direction(self) -> None:
        """§7.1: "the engine must not manufacture direction from noise".

        A flat doji window has no dominant side and almost no ROC. Without the
        cap the body and participation components would still contribute, and
        the engine would report meaningful energy where none exists.
        """
        result = momentum_score(flat(40), 39)

        assert result is not None
        assert result.direction is MomentumDirection.NEUTRAL
        assert result.neutral_capped is True
        assert result.score <= Decimal(35)

    def test_a_strong_trend_is_never_capped(self) -> None:
        result = momentum_score(trending(40), 39)

        assert result is not None
        assert result.neutral_capped is False


class TestBodyDominance:
    def test_a_zero_range_candle_scores_no_conviction(self) -> None:
        """A halted print has no body fraction. Treating 0/0 as 1 would score
        a halt as maximum conviction, which is the worst possible reading.
        """
        series = trending(39)

        series.append(candle(39, open_="300", close="300", high="300", low="300"))

        result = momentum_score(series, 39)

        assert result is not None
        assert Decimal(0) <= result.body_component <= Decimal(25)
