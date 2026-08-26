"""§8.6's classification, and the reason it now keeps.

A classification returning None is what stops a setup publishing whatever its
confidence — and it was the only decision in the pipeline that recorded no
reason for itself. On the staging host that cost real time: 63 of 64 setups
carried a null archetype, one at confidence 77 with every gate passed, and the
database could not say which clause had refused them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import classify_archetype, explain_archetype
from scanner.domain.confluence.archetypes import (
    CLASSIFICATION_ORDER,
    MAX_FVG_AGE_CANDLES,
    RANGE_MIN_ATR,
    Archetype,
    ArchetypeEvidence,
)


def sweep_reversal() -> ArchetypeEvidence:
    return ArchetypeEvidence(
        external_sweep=True,
        mss_confirmed=True,
        mss_origin_zone_retested=True,
        range_extreme_pd=True,
        stop_hunt_confirmed=True,
    )


def fvg_continuation() -> ArchetypeEvidence:
    return ArchetypeEvidence(
        displacement_fvg=True,
        fvg_first_touch=True,
        htf_aligned=True,
        fvg_age_candles=1,
    )


def test_a_match_reports_the_archetype_and_stops_looking() -> None:
    """§8.6 is first-match in table order, and A1 is first.

    `unmet` holds only the archetypes tried *before* the match — the ones
    ruled out on the way. Reporting clauses for archetypes never evaluated
    would be inventing an answer.
    """
    match = explain_archetype(sweep_reversal())

    assert match.archetype is Archetype.SWEEP_REVERSAL
    assert match.matched
    assert match.unmet == {}
    assert match.closest is None


def test_classify_and_explain_can_never_disagree() -> None:
    """The rules are one table read by both.

    They were an `if` chain and a separate question; keeping them apart is how
    a verdict and its reason drift.
    """
    for evidence in (sweep_reversal(), fvg_continuation(), ArchetypeEvidence()):
        assert classify_archetype(evidence) is explain_archetype(evidence).archetype


def test_a_chain_that_fits_nothing_names_every_missing_clause() -> None:
    match = explain_archetype(ArchetypeEvidence())

    assert match.archetype is None
    assert not match.matched
    # Every archetype was tried, and every one has at least one reason.
    assert set(match.unmet) == {a.value for a in CLASSIFICATION_ORDER}
    assert all(clauses for clauses in match.unmet.values())


def test_one_missing_clause_is_reported_alone() -> None:
    """The question a human actually asks: what was I one step away from?"""

    evidence = ArchetypeEvidence(
        external_sweep=True,
        mss_confirmed=True,
        mss_origin_zone_retested=True,
        range_extreme_pd=True,
        # The only one false.
        stop_hunt_confirmed=False,
    )

    match = explain_archetype(evidence)

    assert match.archetype is None
    assert match.unmet["A1"] == ("stop_hunt_confirmed",)
    assert match.closest == "A1"


def test_the_closest_archetype_is_the_one_with_fewest_unmet_clauses() -> None:
    evidence = ArchetypeEvidence(
        # A4 needs four; three hold.
        displacement_fvg=True,
        fvg_first_touch=True,
        htf_aligned=True,
        fvg_age_candles=MAX_FVG_AGE_CANDLES + 1,
    )

    match = explain_archetype(evidence)

    assert match.closest == "A4"
    assert match.unmet["A4"] == ("fvg_age_within_limit",)


def test_closest_is_absent_when_something_matched() -> None:
    """ "Closest" is only meaningful about a failure."""

    assert explain_archetype(sweep_reversal()).closest is None


def test_the_breaker_rule_keeps_its_two_routes() -> None:
    """§8.6 allows the grade *or* the entry-grade confirmation, not both.

    Named as one clause so the explanation says "neither route held" rather
    than reporting two failures for one requirement.
    """
    by_grade = ArchetypeEvidence(
        breaker_formed=True,
        breaker_first_retest_respected=True,
        breaker_grade_a=True,
    )
    by_confirmation = ArchetypeEvidence(
        breaker_formed=True,
        breaker_first_retest_respected=True,
        entry_grade_confirmation=True,
    )

    assert explain_archetype(by_grade).archetype is Archetype.BREAKER_RETEST
    assert explain_archetype(by_confirmation).archetype is Archetype.BREAKER_RETEST

    neither = explain_archetype(
        ArchetypeEvidence(breaker_formed=True, breaker_first_retest_respected=True)
    )

    assert neither.unmet["A2"] == ("breaker_grade_a_or_entry_grade_confirmation",)


def test_a_counter_displaced_leg_is_reported_as_such() -> None:
    """The one negated clause. Reported by its own name so a reader is not
    left wondering which way round the flag runs."""

    evidence = ArchetypeEvidence(
        trend_active=True,
        displaced_bos=True,
        retraced_into_zone=True,
        htf_aligned=True,
        retracement_leg=True,
        counter_displacement=True,
    )

    assert explain_archetype(evidence).unmet["A3"] == ("not_counter_displacement",)


@pytest.mark.parametrize(
    ("age", "matches"),
    [(-1, False), (0, True), (MAX_FVG_AGE_CANDLES, True), (MAX_FVG_AGE_CANDLES + 1, False)],
)
def test_the_fvg_age_bound_is_inclusive_at_both_ends(age: int, matches: bool) -> None:
    evidence = ArchetypeEvidence(
        displacement_fvg=True,
        fvg_first_touch=True,
        htf_aligned=True,
        fvg_age_candles=age,
    )

    assert (explain_archetype(evidence).archetype is Archetype.FVG_CONTINUATION) is matches


def test_the_range_width_bound_is_inclusive() -> None:
    def at(width: Decimal) -> ArchetypeEvidence:
        return ArchetypeEvidence(
            ranging=True,
            range_extreme_swept=True,
            rejection_confirmed=True,
            range_width_atr=width,
        )

    assert explain_archetype(at(RANGE_MIN_ATR)).archetype is Archetype.RANGE_LIQUIDITY_PLAY
    assert explain_archetype(at(RANGE_MIN_ATR - Decimal("0.01"))).archetype is None
