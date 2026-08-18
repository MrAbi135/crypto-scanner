"""Leg segmentation against SLS §7.5, and trend strength against §7.4."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.domain.momentum import (
    Leg,
    LegKind,
    MomentumDirection,
    anchoring_legs,
    segment_legs,
    trend_strength,
)
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def candles(count: int = 60, *, span: str = "10"):
    """Flat-ranged history so ATR is a known, stable 10."""
    reach = Decimal(span)

    return [
        make_candle(
            timeframe=Timeframe.H4,
            open_time=BASE + Timeframe.H4.duration * i,
            open_=Decimal(100),
            close=Decimal(100),
            high=Decimal(100) + reach / 2,
            low=Decimal(100) - reach / 2,
        )
        for i in range(count)
    ]


def swing(index: int, price: str, kind: SwingKind = SwingKind.LOW) -> SwingPoint:
    return SwingPoint(
        index,
        BASE + Timeframe.H4.duration * index,
        Decimal(price),
        kind,
        SwingStrength.INTERNAL,
    )


class TestImpulse:
    def test_displacement_plus_progress_is_an_impulse(self) -> None:
        """§7.5 requires both: >= 1 displacement candle AND >= 1.5 x ATR."""
        legs = segment_legs(
            candles(),
            [swing(20, "100"), swing(30, "130", SwingKind.HIGH)],
            frozenset({25}),
        )

        assert len(legs) == 1
        assert legs[0].kind is LegKind.IMPULSE
        assert legs[0].net_progress_atr == Decimal(3)

    def test_progress_without_displacement_is_not_an_impulse(self) -> None:
        """Both conditions, not either. A drift that covers ground without a
        displacement candle is not the institutional move §7.5 describes.
        """
        legs = segment_legs(
            candles(),
            [swing(20, "100"), swing(30, "130", SwingKind.HIGH)],
            frozenset(),
        )

        assert legs[0].kind is not LegKind.IMPULSE

    def test_displacement_without_progress_is_not_an_impulse(self) -> None:
        legs = segment_legs(
            candles(),
            [swing(20, "100"), swing(30, "110", SwingKind.HIGH)],
            frozenset({25}),
        )

        assert legs[0].kind is not LegKind.IMPULSE


class TestRetracement:
    def _after_impulse(self, pullback_to: str, *, displaced_pullback: bool = False):
        return segment_legs(
            candles(),
            [
                swing(20, "100"),
                swing(30, "130", SwingKind.HIGH),
                swing(40, pullback_to),
            ],
            frozenset({25}) | (frozenset({35}) if displaced_pullback else frozenset()),
        )

    def test_a_shallow_counter_leg_retraces(self) -> None:
        legs = self._after_impulse("115")

        assert legs[0].kind is LegKind.IMPULSE
        assert legs[1].kind is LegKind.RETRACEMENT
        assert legs[1].retrace_fraction == Decimal("0.5")

    def test_a_counter_leg_past_one_hundred_percent_escalates(self) -> None:
        """§7.5: a counter-leg retracing > 100% is neither impulse nor
        retracement -- it escalates to §3.6. Filing it as a deep pullback would
        record a reversal as a continuation.
        """
        legs = self._after_impulse("95")

        assert legs[1].kind is LegKind.ESCALATE
        assert legs[1].retrace_fraction is not None
        assert legs[1].retrace_fraction > Decimal(1)

    def test_a_displaced_counter_leg_escalates_even_when_shallow(self) -> None:
        """Displacement against the trend is a structure event at any depth."""
        legs = self._after_impulse("115", displaced_pullback=True)

        assert legs[1].kind is LegKind.ESCALATE
        assert legs[1].retrace_fraction == Decimal("0.5")


class TestMicro:
    def test_a_leg_below_three_quarters_atr_is_micro(self) -> None:
        legs = segment_legs(
            candles(),
            [swing(20, "100"), swing(30, "105", SwingKind.HIGH)],
            frozenset(),
        )

        assert legs[0].kind is LegKind.MICRO

    def test_micro_legs_never_anchor(self) -> None:
        """§7.5 excludes them from trend strength and OTE. Chop must not anchor."""
        legs = segment_legs(
            candles(),
            [swing(20, "100"), swing(30, "105", SwingKind.HIGH), swing(40, "135")],
            frozenset({35}),
        )

        assert LegKind.MICRO in {leg.kind for leg in legs}
        assert all(leg.kind is not LegKind.MICRO for leg in anchoring_legs(legs))


class TestSegmentation:
    def test_one_swing_makes_no_leg(self) -> None:
        assert segment_legs(candles(), [swing(20, "100")], frozenset()) == ()

    def test_swings_are_ordered_before_pairing(self) -> None:
        """Legs must run oldest-first regardless of input order, or a leg would
        be measured backwards and its direction inverted.
        """
        legs = segment_legs(
            candles(),
            [swing(30, "130", SwingKind.HIGH), swing(20, "100")],
            frozenset({25}),
        )

        assert legs[0].start_index == 20
        assert legs[0].direction == "UP"


class TestTrendStrength:
    def _retrace(self, fraction: str) -> Leg:
        return Leg(
            start_index=0,
            end_index=1,
            start_price=Decimal(100),
            end_price=Decimal(90),
            kind=LegKind.RETRACEMENT,
            displaced=False,
            net_progress_atr=Decimal(1),
            retrace_fraction=Decimal(fraction),
        )

    def test_a_full_strength_trend_reaches_one_hundred(self) -> None:
        result = trend_strength(
            unbroken_pairs=4,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=[self._retrace("0.5")],
        )

        assert result.structural == Decimal(40)
        assert result.alignment == Decimal(30)
        assert result.pullback == Decimal(30)
        assert result.total == Decimal(100)

    def test_the_total_is_the_sum_of_its_parts(self) -> None:
        result = trend_strength(
            unbroken_pairs=2,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=[self._retrace("0.7")],
        )

        assert result.total == result.structural + result.alignment + result.pullback

    def test_opposed_momentum_earns_no_alignment(self) -> None:
        result = trend_strength(
            unbroken_pairs=4,
            trend_direction="UP",
            momentum_direction=MomentumDirection.DOWN,
            legs=[],
        )

        assert result.alignment == Decimal(0)

    def test_a_ranging_trend_has_nothing_to_align_with(self) -> None:
        """RANGING is not disagreement, but it is not alignment either."""
        result = trend_strength(
            unbroken_pairs=4,
            trend_direction="RANGING",
            momentum_direction=MomentumDirection.UP,
            legs=[],
        )

        assert result.alignment == Decimal(0)

    @pytest.mark.parametrize(
        ("fraction", "expected"),
        [("0.5", "30"), ("0.62", "30"), ("0.79", "0"), ("0.9", "0")],
    )
    def test_pullback_scores_across_the_ote_band(self, fraction: str, expected: str) -> None:
        result = trend_strength(
            unbroken_pairs=0,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=[self._retrace(fraction)],
        )

        assert result.pullback == Decimal(expected)

    def test_only_the_last_three_retracements_count(self) -> None:
        """§7.4 says "last 3 legs" -- an old shallow pullback must not flatter
        a trend whose recent ones are deep.
        """
        legs = [self._retrace("0.1")] + [self._retrace("0.9")] * 3

        result = trend_strength(
            unbroken_pairs=0,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=legs,
        )

        assert result.legs_counted == 3
        assert result.pullback == Decimal(0)

    def test_escalated_and_micro_legs_are_not_averaged_in(self) -> None:
        escalated = Leg(
            start_index=0,
            end_index=1,
            start_price=Decimal(100),
            end_price=Decimal(80),
            kind=LegKind.ESCALATE,
            displaced=True,
            net_progress_atr=Decimal(2),
            retrace_fraction=Decimal("1.5"),
        )

        result = trend_strength(
            unbroken_pairs=0,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=[escalated, self._retrace("0.5")],
        )

        assert result.legs_counted == 1
        assert result.pullback == Decimal(30)

    def test_no_legs_earns_no_pullback_credit(self) -> None:
        result = trend_strength(
            unbroken_pairs=4,
            trend_direction="UP",
            momentum_direction=MomentumDirection.UP,
            legs=[],
        )

        assert result.pullback == Decimal(0)
        assert result.total == Decimal(70)
