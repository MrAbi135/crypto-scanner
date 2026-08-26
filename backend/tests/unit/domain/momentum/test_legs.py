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


class TestTheAnchorFollowsTheMarket:
    """`previous_impulse` must not outlive the structure it describes.

    Every other test in this file stops at two legs, which is one short of
    where the bug lived: it takes an impulse, a reversal, and then a *third*
    leg to see that the anchor never moved. The consequence was a one-way
    ratchet -- a counter-direction leg can never be classified `IMPULSE`
    (escalation and retracement are both checked first), and only an `IMPULSE`
    re-anchored, so whichever direction printed the window's first impulse held
    the anchor for the rest of the window and the other direction was locked
    out of `IMPULSE` entirely.

    On the host that showed up as ETHUSDT H1 with 9 down impulses and zero up,
    BTCUSDT H1 with 12 up and zero down, and `ESCALATE` filling the locked-out
    side exactly -- so §8.6 A3, which wants a displaced BOS inside the latest
    impulse leg in the candidate's direction, could never match one of the two
    directions on any symbol.
    """

    def test_a_reversal_lets_the_new_direction_impulse(self) -> None:
        """The ratchet, stated as the property it broke.

        Up-impulse, a counter-leg past 100% (§7.5's escalation), a shallow
        bounce, then a real down move. That last leg is an impulse by every
        measure §7.5 names, and before the anchor followed the reversal it came
        out `ESCALATE` -- measured against a leg the market had left behind.
        """
        legs = segment_legs(
            candles(),
            [
                swing(20, "100"),
                swing(30, "130", SwingKind.HIGH),
                swing(40, "95"),
                swing(50, "110", SwingKind.HIGH),
                swing(58, "65"),
            ],
            frozenset({25, 55}),
        )

        assert [(leg.kind, leg.direction) for leg in legs] == [
            (LegKind.IMPULSE, "UP"),
            (LegKind.ESCALATE, "DOWN"),
            (LegKind.RETRACEMENT, "UP"),
            (LegKind.IMPULSE, "DOWN"),
        ]

    def test_the_bounce_is_measured_against_the_reversal_not_the_old_impulse(
        self,
    ) -> None:
        """The same move, read as a fraction of the right leg.

        130 -> 95 then back to 110 is 43% of the reversal. Against the
        abandoned up-impulse it would be 50% -- a different number describing a
        different pullback, and the one OTE would have anchored on.
        """
        legs = segment_legs(
            candles(),
            [
                swing(20, "100"),
                swing(30, "130", SwingKind.HIGH),
                swing(40, "95"),
                swing(50, "110", SwingKind.HIGH),
            ],
            frozenset({25}),
        )

        assert legs[2].retrace_fraction == Decimal(15) / Decimal(35)

    def test_a_small_counter_displaced_leg_does_not_take_the_anchor(self) -> None:
        """§7.5 escalates a counter-displaced leg "regardless of how small it
        is" so §3.6 can see it. Small is a thing to report, not a thing to
        measure the next pullback against -- otherwise noise re-anchors the
        window.

        The third leg is the tell: with the anchor still on the up-impulse it
        runs with the trend and is an impulse; had the 0.6-ATR blip taken the
        anchor, it would be counter to it and escalate instead.
        """
        legs = segment_legs(
            candles(),
            [
                swing(20, "100"),
                swing(30, "130", SwingKind.HIGH),
                swing(40, "124"),
                swing(50, "160", SwingKind.HIGH),
            ],
            frozenset({25, 35, 45}),
        )

        assert legs[1].kind is LegKind.ESCALATE
        assert legs[1].net_progress_atr < Decimal("1.5")
        assert (legs[2].kind, legs[2].direction) == (LegKind.IMPULSE, "UP")

    def test_neither_direction_is_locked_out_of_one_window(self) -> None:
        """A rally, a reversal, and a fall -- impulses on both sides of it.

        This is the shape the host actually prints and the shape no fixture
        here had: a *run* in one direction, then a run in the other. A pure
        zigzag would not do, and asserting it would be wrong rather than
        merely weak -- every leg of an alternation is counter to the one before
        it, so after the first there is no continuation leg to be an impulse.
        The ratchet is about the anchor going stale, not about zigzags.
        """
        legs = segment_legs(
            candles(),
            [
                swing(10, "100"),
                swing(18, "140", SwingKind.HIGH),
                swing(26, "135"),
                swing(34, "180", SwingKind.HIGH),
                swing(42, "120"),
                swing(50, "130", SwingKind.HIGH),
                swing(58, "80"),
            ],
            frozenset({15, 30, 55}),
        )

        assert [(leg.kind, leg.direction) for leg in legs] == [
            (LegKind.IMPULSE, "UP"),
            (LegKind.MICRO, "DOWN"),
            (LegKind.IMPULSE, "UP"),
            (LegKind.ESCALATE, "DOWN"),
            (LegKind.RETRACEMENT, "UP"),
            # Before the anchor followed the reversal this was `ESCALATE`,
            # measured as a 111% retracement of an up-leg two reversals back.
            (LegKind.IMPULSE, "DOWN"),
        ]


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
