"""Confidence arithmetic against SLS §8.4, §8.5, §8.7 and §9.

§8.7 calls its worked example "the canonical unit-test fixture". It is the one
place the spec does the arithmetic itself, so it is reproduced here exactly --
every intermediate value, not just the total.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    MAX_PENALTY,
    MAX_SYNERGY,
    WEIGHTS,
    Adjustment,
    Factor,
    Grade,
    base_confidence,
    final_confidence,
    grade,
)


def factors(f1="85", f2="80", f3="90", f4="70", f5="65", f6="100"):
    return {
        Factor.STRUCTURE: Decimal(f1),
        Factor.LIQUIDITY: Decimal(f2),
        Factor.ZONE: Decimal(f3),
        Factor.VOLUME: Decimal(f4),
        Factor.MOMENTUM: Decimal(f5),
        Factor.HTF_ALIGNMENT: Decimal(f6),
    }


class TestWorkedExample:
    """SLS §8.7, reproduced term by term."""

    def test_each_weighted_term_matches_the_spec(self) -> None:
        # 85x.20 + 80x.15 + 90x.20 + 70x.15 + 65x.15 + 100x.15
        #  = 17  +  12   +  18   + 10.5  + 9.75  +  15
        expected = {
            Factor.STRUCTURE: Decimal("17"),
            Factor.LIQUIDITY: Decimal("12"),
            Factor.ZONE: Decimal("18"),
            Factor.VOLUME: Decimal("10.5"),
            Factor.MOMENTUM: Decimal("9.75"),
            Factor.HTF_ALIGNMENT: Decimal("15"),
        }

        supplied = factors()

        for factor, term in expected.items():
            assert supplied[factor] * WEIGHTS[factor] == term

    def test_base_confidence_is_82_25(self) -> None:
        assert base_confidence(factors()) == Decimal("82.25")

    def test_the_two_synergies_carry_it_to_95(self) -> None:
        """Zone-stack +5 and sweep-chain +8 give 95.25, published as 95."""
        result = final_confidence(
            factors(),
            [
                Adjustment("zone_stack", Decimal(5)),
                Adjustment("sweep_chain", Decimal(8)),
            ],
        )

        assert result.base == Decimal("82.25")
        assert result.synergy == Decimal(13)
        assert result.penalty == Decimal(0)
        assert result.final == Decimal(95)

    def test_the_result_is_grade_s(self) -> None:
        result = final_confidence(
            factors(),
            [Adjustment("zone_stack", Decimal(5)), Adjustment("sweep_chain", Decimal(8))],
        )

        assert result.published_grade is Grade.S


class TestFlooring:
    def test_a_fraction_never_lifts_a_grade(self) -> None:
        """§8.7 carries 95.25 to 95, so the rule is floor, not round.

        Rounding would let 89.5 publish as Grade A on evidence worth 89.
        """
        result = final_confidence(factors(f1="89", f2="89", f3="89", f4="89", f5="89", f6="90"))

        assert result.base > Decimal("89")
        assert result.final == Decimal(89)
        assert result.published_grade is Grade.A


class TestBounds:
    def test_synergy_is_capped_at_fifteen(self) -> None:
        """§8.5. Adjustments refine a score; they never rescue one.

        A setup needing +40 to reach a floor did not earn the floor.
        """
        result = final_confidence(
            factors(),
            [Adjustment("a", Decimal(8)), Adjustment("b", Decimal(8)), Adjustment("c", Decimal(8))],
        )

        assert result.synergy == MAX_SYNERGY
        assert result.synergy_capped is True

    def test_penalty_is_capped_at_twenty(self) -> None:
        result = final_confidence(
            factors(),
            [
                Adjustment("x", Decimal(-8)),
                Adjustment("y", Decimal(-8)),
                Adjustment("z", Decimal(-8)),
            ],
        )

        assert result.penalty == MAX_PENALTY
        assert result.penalty_capped is True

    def test_the_final_score_is_clamped_to_the_range(self) -> None:
        top = final_confidence(
            factors(f1="100", f2="100", f3="100", f4="100", f5="100", f6="100"),
            [Adjustment("a", Decimal(15))],
        )

        assert top.final == Decimal(100)

        bottom = final_confidence(
            factors(f1="0", f2="0", f3="0", f4="0", f5="0", f6="0"),
            [Adjustment("x", Decimal(-20))],
        )

        assert bottom.final == Decimal(0)

    def test_every_adjustment_stays_itemised(self) -> None:
        """§8.5 requires them "itemized in the evidence record".

        Kept even when capped: the cap changes the total, not the record of
        what was claimed.
        """
        applied = [Adjustment("a", Decimal(8), "evt-1"), Adjustment("b", Decimal(8), "evt-2")]

        result = final_confidence(factors(), applied)

        assert [a.code for a in result.applied] == ["a", "b"]
        assert result.applied[0].evidence_id == "evt-1"


class TestInputDiscipline:
    def test_a_missing_factor_is_refused_not_scored_as_zero(self) -> None:
        """Absence of evidence is not evidence of zero.

        Scoring a missing factor as 0 would publish a confidence the engine
        never computed -- plausible, wrong, and silent.
        """
        incomplete = factors()
        del incomplete[Factor.VOLUME]

        with pytest.raises(ValueError, match="F4"):
            base_confidence(incomplete)

    @pytest.mark.parametrize("bad", ["-1", "101"])
    def test_a_factor_outside_zero_to_hundred_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError, match="outside 0-100"):
            base_confidence(factors(f4=bad))


class TestGrades:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            ("100", Grade.S),
            ("90", Grade.S),
            ("89", Grade.A),
            ("80", Grade.A),
            ("79", Grade.B),
            ("70", Grade.B),
            ("69", None),
            ("0", None),
        ],
    )
    def test_every_band_edge(self, score: str, expected: Grade | None) -> None:
        """§9.4. Below 70 is not a weak grade -- it is never published."""
        assert grade(Decimal(score)) is expected
