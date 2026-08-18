"""Factor point tables against SLS §8.3.1 (v1.0.5).

The amendment justifies its numbers by reproducing §8.7's worked example. That
claim is executable, so it is executed here — if a table drifts, the anchor
check fails before anything else does.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    ZONE_GRADE_POINTS,
    ZONE_STATE_POINTS,
    LiquidityEvidence,
    MomentumEvidence,
    StructureEvidence,
    ZoneEvidence,
    liquidity_factor,
    momentum_factor,
    structure_factor,
    zone_factor,
)


class TestAnchorAgainstTheWorkedExample:
    """§8.3.1's own anchor table, executed.

    §8.7 describes: H4 and D1 both BULLISH, external SSL sweep at 0.8 ATR, no
    MSS (continuation), displaced BOS up, retrace into an OTE ∩ OB_A stack.
    """

    def test_f1_scores_82_5(self) -> None:
        result = structure_factor(
            StructureEvidence(
                break_confirmed=True,
                displaced=True,
                external=True,
                mss=False,
                unbroken_pairs=3,
                failed_breaks=0,
            )
        )

        assert result.score == Decimal("82.5")

    def test_f1_brackets_the_spec_figure_without_reaching_it(self) -> None:
        """§8.7's 85 is unreachable by pair count, and §8.3.1 says so.

        Three pairs give 82.5, four give 90. The illustration sits between two
        adjacent states of this table. It is left that way deliberately: §8.7 is
        a Normative Illustration of the *combination* arithmetic, which §8.4
        reproduces exactly, not of factor internals that did not exist when it
        was written.

        This test exists because the amendment's first draft claimed the gap
        closed at four pairs. It does not. The arithmetic had not been run.
        """

        def f1(pairs: int) -> Decimal:
            return structure_factor(
                StructureEvidence(
                    break_confirmed=True,
                    displaced=True,
                    external=True,
                    unbroken_pairs=pairs,
                    failed_breaks=0,
                )
            ).score

        assert f1(3) == Decimal("82.5")
        assert f1(4) == Decimal(90)

        assert all(f1(p) != Decimal(85) for p in range(0, 9))

    def test_f2_scores_76_6(self) -> None:
        result = liquidity_factor(
            LiquidityEvidence(
                sweep_confirmed=True,
                external=True,
                depth_atr=Decimal("0.8"),
                unclaimed=True,
                fresh=True,
                stop_hunt=False,
                target_pool_strength=Decimal(76),
            )
        )

        assert result.score == Decimal("76.6")

    def test_f2_reaches_the_spec_figure_at_pool_strength_90(self) -> None:
        result = liquidity_factor(
            LiquidityEvidence(
                sweep_confirmed=True,
                external=True,
                depth_atr=Decimal("0.8"),
                unclaimed=True,
                fresh=True,
                target_pool_strength=Decimal(90),
            )
        )

        assert result.score == Decimal("80.1")

    def test_f3_reproduces_ninety_exactly(self) -> None:
        result = zone_factor(
            ZoneEvidence(
                grade="OB_A",
                state="FRESH",
                stack_depth=2,
                entry_confirmation=True,
            )
        )

        assert result.score == Decimal(90)

    def test_f5_reproduces_sixty_five_exactly(self) -> None:
        result = momentum_factor(
            MomentumEvidence(
                score=Decimal(60),
                aligned=True,
                accelerating=False,
                decelerating=False,
                exhaustion_against=False,
            )
        )

        assert result.score == Decimal(65)


class TestOrderingsTheSpecAsserts:
    def test_displacement_outranks_externality(self) -> None:
        """§8.3.1: displacement is evidence of intent; external describes only
        which swing set was crossed.
        """
        displaced = structure_factor(StructureEvidence(break_confirmed=True, displaced=True))
        external = structure_factor(StructureEvidence(break_confirmed=True, external=True))

        assert displaced.score > external.score

    def test_the_zone_grade_ladder_is_strictly_descending(self) -> None:
        """§8.3: BRK_A > OB_A > OB_B > FVG > MIT > IFVG."""
        ladder = ["BRK_A", "OB_A", "OB_B", "FVG", "MIT", "IFVG"]

        points = [ZONE_GRADE_POINTS[g] for g in ladder]

        assert points == sorted(points, reverse=True)
        assert len(set(points)) == len(points)

    def test_fresh_outranks_tested(self) -> None:
        assert ZONE_STATE_POINTS["FRESH"] > ZONE_STATE_POINTS["TESTED"]


class TestDeliberateZeroes:
    def test_opposed_momentum_earns_nothing_from_its_largest_component(self) -> None:
        """§8.3.1: a score pointing the other way is absence of support, not
        weak support. Scaling it down instead would let a trade against
        momentum still collect most of the factor.
        """
        opposed = momentum_factor(MomentumEvidence(score=Decimal(90), aligned=False))

        assert all(c.code != "aligned_momentum" for c in opposed.contributions)

    def test_a_reclaimed_sweep_loses_the_whole_unclaimed_award(self) -> None:
        """§4.6 calls it contrary evidence; partial credit would still read as
        support for the setup it undermines.
        """
        unclaimed = liquidity_factor(LiquidityEvidence(sweep_confirmed=True, unclaimed=True))
        reclaimed = liquidity_factor(LiquidityEvidence(sweep_confirmed=True, unclaimed=False))

        assert unclaimed.score - reclaimed.score == Decimal(6)

    def test_exhaustion_against_the_trade_removes_a_flat_twenty(self) -> None:
        clean = momentum_factor(MomentumEvidence(score=Decimal(60), aligned=True))
        tired = momentum_factor(
            MomentumEvidence(score=Decimal(60), aligned=True, exhaustion_against=True)
        )

        assert clean.score - tired.score == Decimal(20)


class TestBoundsAndCaps:
    def test_stack_depth_stops_paying_after_two(self) -> None:
        """§8.3.1: §8.5 already awards a zone-stack synergy bonus, so an
        unbounded term here would reward piling weak zones together.
        """
        two = zone_factor(ZoneEvidence(grade="FVG", state="FRESH", stack_depth=2))
        five = zone_factor(ZoneEvidence(grade="FVG", state="FRESH", stack_depth=5))

        assert two.score == five.score

    def test_sweep_depth_saturates_at_one_atr(self) -> None:
        at_one = liquidity_factor(LiquidityEvidence(sweep_confirmed=True, depth_atr=Decimal(1)))
        at_three = liquidity_factor(LiquidityEvidence(sweep_confirmed=True, depth_atr=Decimal(3)))

        assert at_one.score == at_three.score

    def test_a_perfect_setup_reaches_but_does_not_exceed_one_hundred(self) -> None:
        best = liquidity_factor(
            LiquidityEvidence(
                sweep_confirmed=True,
                external=True,
                depth_atr=Decimal(1),
                unclaimed=True,
                fresh=True,
                stop_hunt=True,
                target_pool_strength=Decimal(100),
            )
        )

        assert best.score == Decimal(100)

    def test_no_evidence_scores_zero(self) -> None:
        for factor in (
            structure_factor(StructureEvidence(failed_breaks=2)),
            liquidity_factor(LiquidityEvidence()),
            zone_factor(ZoneEvidence()),
        ):
            assert factor.score == Decimal(0)

    def test_every_contribution_can_carry_an_evidence_id(self) -> None:
        """§8.3: "reproducible from the evidence alone" needs the ids kept."""
        result = structure_factor(
            StructureEvidence(
                break_confirmed=True,
                displaced=True,
                evidence_ids={"displaced": "evt-disp"},
            )
        )

        by_code = {c.code: c for c in result.contributions}

        assert by_code["displaced"].evidence_id == "evt-disp"


class TestTableIntegrity:
    def test_the_tables_import_without_tripping_their_own_guard(self) -> None:
        """Each factor's components must be able to reach its ceiling.

        A table that cannot silently caps the factor below the weight §9.1
        assigns it, rescaling every score with nothing visible changing.
        """
        import scanner.domain.confluence.factor_points as fp

        fp._assert_reaches_one_hundred()

    @pytest.mark.parametrize(
        "grade",
        ["BRK_A", "OB_A", "OB_B", "FVG", "MIT", "IFVG"],
    )
    def test_every_grade_named_by_the_spec_is_priced(self, grade: str) -> None:
        assert grade in ZONE_GRADE_POINTS
