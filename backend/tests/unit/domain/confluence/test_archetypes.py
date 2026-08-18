"""Archetype classification against SLS §8.6, and its floors."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    CLASSIFICATION_ORDER,
    FLOORS,
    RANKING_PRIORITY,
    Archetype,
    ArchetypeEvidence,
    classify_archetype,
    meets_floor,
    ranking_priority,
)


def a1(**over):
    return ArchetypeEvidence(
        **{
            "external_sweep": True,
            "mss_confirmed": True,
            "mss_origin_zone_retested": True,
            "range_extreme_pd": True,
            "stop_hunt_confirmed": True,
            **over,
        }
    )


def a2(**over):
    return ArchetypeEvidence(
        **{
            "breaker_formed": True,
            "breaker_first_retest_respected": True,
            "breaker_grade_a": True,
            **over,
        }
    )


def a3(**over):
    return ArchetypeEvidence(
        **{
            "trend_active": True,
            "displaced_bos": True,
            "retraced_into_zone": True,
            "htf_aligned": True,
            "retracement_leg": True,
            **over,
        }
    )


def a4(**over):
    return ArchetypeEvidence(
        **{
            "displacement_fvg": True,
            "fvg_first_touch": True,
            "htf_aligned": True,
            "fvg_age_candles": 10,
            **over,
        }
    )


def a5(**over):
    return ArchetypeEvidence(
        **{
            "ranging": True,
            "range_extreme_swept": True,
            "rejection_confirmed": True,
            "range_width_atr": Decimal(3),
            **over,
        }
    )


class TestEachChain:
    @pytest.mark.parametrize(
        ("evidence", "expected"),
        [
            (a1(), Archetype.SWEEP_REVERSAL),
            (a2(), Archetype.BREAKER_RETEST),
            (a3(), Archetype.CONTINUATION_PULLBACK),
            (a4(), Archetype.FVG_CONTINUATION),
            (a5(), Archetype.RANGE_LIQUIDITY_PLAY),
        ],
    )
    def test_a_complete_chain_classifies(self, evidence, expected) -> None:
        assert classify_archetype(evidence) is expected

    def test_an_unmatched_chain_is_not_a_setup(self) -> None:
        """§8.6: every publishable setup matches exactly one archetype.

        None means "not a setup", not "a setup of unknown type".
        """
        assert classify_archetype(ArchetypeEvidence()) is None


class TestRequiredConditions:
    def test_a_sweep_reversal_needs_its_stop_hunt(self) -> None:
        assert classify_archetype(a1(stop_hunt_confirmed=False)) is None

    def test_a_sweep_reversal_needs_a_range_extreme(self) -> None:
        assert classify_archetype(a1(range_extreme_pd=False)) is None

    def test_a_breaker_takes_either_route_to_quality(self) -> None:
        """§8.6 asks for BRK_A grade **or** entry-grade Confirmation.

        Two routes to the same claim; requiring both would reject half the
        setups the clause admits.
        """
        by_grade = a2(breaker_grade_a=True, entry_grade_confirmation=False)
        by_confirmation = a2(breaker_grade_a=False, entry_grade_confirmation=True)

        assert classify_archetype(by_grade) is Archetype.BREAKER_RETEST
        assert classify_archetype(by_confirmation) is Archetype.BREAKER_RETEST

        assert classify_archetype(a2(breaker_grade_a=False)) is None

    def test_a_counter_displaced_leg_is_not_a_pullback(self) -> None:
        """§8.6 asks for a retracement leg, NOT counter-displacement.

        A counter-displaced leg is CHoCH territory (§3.6). Classifying it as a
        continuation would read a reversal as a trade with the trend.
        """
        assert classify_archetype(a3(counter_displacement=True)) is None

    def test_a_continuation_needs_htf_alignment(self) -> None:
        assert classify_archetype(a3(htf_aligned=False)) is None
        assert classify_archetype(a4(htf_aligned=False)) is None

    @pytest.mark.parametrize("age", [0, 30])
    def test_an_fvg_inside_the_age_budget_qualifies(self, age: int) -> None:
        assert classify_archetype(a4(fvg_age_candles=age)) is Archetype.FVG_CONTINUATION

    def test_an_fvg_past_thirty_candles_does_not(self) -> None:
        assert classify_archetype(a4(fvg_age_candles=31)) is None

    @pytest.mark.parametrize(("width", "matches"), [("2", True), ("1.99", False)])
    def test_a_range_must_be_at_least_two_atr(self, width: str, matches: bool) -> None:
        result = classify_archetype(a5(range_width_atr=Decimal(width)))

        assert (result is Archetype.RANGE_LIQUIDITY_PLAY) is matches


class TestFirstMatchWins:
    def test_a_chain_satisfying_two_archetypes_takes_the_earlier(self) -> None:
        """§8.6 is rule-ordered, first match wins, in table order.

        A1's chain and A3's can both be present on one candidate; without a
        defined order the classification would depend on iteration order.
        """
        both = ArchetypeEvidence(
            external_sweep=True,
            mss_confirmed=True,
            mss_origin_zone_retested=True,
            range_extreme_pd=True,
            stop_hunt_confirmed=True,
            trend_active=True,
            displaced_bos=True,
            retraced_into_zone=True,
            htf_aligned=True,
            retracement_leg=True,
        )

        assert classify_archetype(both) is Archetype.SWEEP_REVERSAL


class TestOrderings:
    def test_classification_follows_the_table(self) -> None:
        assert CLASSIFICATION_ORDER == (
            Archetype.SWEEP_REVERSAL,
            Archetype.BREAKER_RETEST,
            Archetype.CONTINUATION_PULLBACK,
            Archetype.FVG_CONTINUATION,
            Archetype.RANGE_LIQUIDITY_PLAY,
        )

    def test_ranking_priority_is_a_different_order(self) -> None:
        """§9.2 ranks A1 > A2 > A5 > A3 > A4 -- reversal classes are rarer and
        time-critical. This is NOT the classification order, and using one for
        the other would silently reorder every tied result on the board.
        """
        assert RANKING_PRIORITY == (
            Archetype.SWEEP_REVERSAL,
            Archetype.BREAKER_RETEST,
            Archetype.RANGE_LIQUIDITY_PLAY,
            Archetype.CONTINUATION_PULLBACK,
            Archetype.FVG_CONTINUATION,
        )

        assert RANKING_PRIORITY != CLASSIFICATION_ORDER

    def test_a_range_play_outranks_a_continuation_on_a_tie(self) -> None:
        assert ranking_priority(Archetype.RANGE_LIQUIDITY_PLAY) < ranking_priority(
            Archetype.CONTINUATION_PULLBACK
        )


class TestFloors:
    @pytest.mark.parametrize(
        ("archetype", "floor"),
        [
            (Archetype.SWEEP_REVERSAL, "75"),
            (Archetype.BREAKER_RETEST, "72"),
            (Archetype.CONTINUATION_PULLBACK, "70"),
            (Archetype.FVG_CONTINUATION, "70"),
            (Archetype.RANGE_LIQUIDITY_PLAY, "74"),
        ],
    )
    def test_each_floor_matches_the_spec(self, archetype: Archetype, floor: str) -> None:
        assert FLOORS[archetype] == Decimal(floor)

    def test_the_floor_is_inclusive(self) -> None:
        assert meets_floor(Archetype.SWEEP_REVERSAL, Decimal(75)) is True
        assert meets_floor(Archetype.SWEEP_REVERSAL, Decimal(74)) is False

    def test_a_reversal_needs_more_than_a_continuation(self) -> None:
        """The floors encode that trading against the state costs more evidence."""
        assert FLOORS[Archetype.SWEEP_REVERSAL] > FLOORS[Archetype.CONTINUATION_PULLBACK]

    def test_every_archetype_has_a_floor(self) -> None:
        assert set(FLOORS) == set(Archetype)
