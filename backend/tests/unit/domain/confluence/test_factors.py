"""Evidence factors against SLS §8.3, and the Volume Factor Score against §6.7."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    Contribution,
    Factor,
    FactorScore,
    from_contributions,
    htf_alignment_factor,
    volume_factor,
)
from scanner.domain.volume import VolumeFactorEvidence, volume_factor_score


class TestContributionFramework:
    def test_a_factor_is_the_sum_of_its_contributions(self) -> None:
        """§8.3: "a sum of enumerated evidence contributions"."""
        result = from_contributions(
            Factor.STRUCTURE,
            (
                Contribution("displaced_bos", Decimal(40), "evt-1"),
                Contribution("external_break", Decimal(25), "evt-2"),
            ),
        )

        assert result.score == Decimal(65)

    def test_contributions_survive_the_clamp(self) -> None:
        """A factor that would have scored 130 is different evidence from one
        that scored exactly 100, and §8.3 wants the record either way.
        """
        result = from_contributions(
            Factor.STRUCTURE,
            (
                Contribution("a", Decimal(80)),
                Contribution("b", Decimal(50)),
            ),
        )

        assert result.score == Decimal(100)
        assert len(result.contributions) == 2
        assert sum(c.points for c in result.contributions) == Decimal(130)

    def test_a_negative_sum_floors_at_zero(self) -> None:
        result = from_contributions(
            Factor.MOMENTUM,
            (Contribution("penalty", Decimal(-30)),),
        )

        assert result.score == Decimal(0)

    def test_an_out_of_range_score_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="outside 0-100"):
            FactorScore(factor=Factor.ZONE, score=Decimal(101))


class TestF4Volume:
    def test_it_passes_the_published_score_through_unmodified(self) -> None:
        """§8.3 says "Volume Factor Score as published".

        Re-deriving or adjusting it here would give the platform two volume
        scores that could disagree with each other.
        """
        result = volume_factor(Decimal(70), "evt-vol")

        assert result.factor is Factor.VOLUME
        assert result.score == Decimal(70)
        assert result.contributions[0].evidence_id == "evt-vol"


class TestF6HtfAlignment:
    @pytest.mark.parametrize(
        ("htf_state", "direction", "expected"),
        [
            ("UP", "UP", "100"),
            ("DOWN", "DOWN", "100"),
            ("CAUTION_UP", "UP", "70"),
            ("RANGING", "UP", "50"),
            ("RANGING", "DOWN", "50"),
            ("DOWN", "UP", "0"),
            ("UP", "DOWN", "0"),
        ],
    )
    def test_the_four_value_table(self, htf_state: str, direction: str, expected: str) -> None:
        """§8.3 F6: aligned=100, CAUTION toward D=70, RANGING=50, opposed=0."""
        assert htf_alignment_factor(htf_state=htf_state, direction=direction).score == Decimal(
            expected
        )

    def test_caution_away_from_the_trade_is_not_partial_support(self) -> None:
        """CAUTION carries its own direction.

        Collapsing both cautions to one value would score an HTF turning
        *against* the trade as though it were leaning into it.
        """
        toward = htf_alignment_factor(htf_state="CAUTION_UP", direction="UP")
        away = htf_alignment_factor(htf_state="CAUTION_DOWN", direction="UP")

        assert toward.score == Decimal(70)
        assert away.score == Decimal(0)

    def test_a_directionless_candidate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="UP or DOWN"):
            htf_alignment_factor(htf_state="UP", direction="RANGING")


class TestVolumeFactorScore:
    def test_a_neutral_candle_scores_the_base(self) -> None:
        assert volume_factor_score(VolumeFactorEvidence()).score == Decimal(50)

    def test_each_bonus_matches_the_spec(self) -> None:
        """§6.7: +15 spike, +15 institutional, +10 expansion, +5 stealth."""
        result = volume_factor_score(
            VolumeFactorEvidence(
                spike_aligned=True,
                institutional_volume=True,
                expansion_aligned=True,
                stealth_flow=True,
            )
        )

        assert result.score == Decimal(95)

    def test_each_penalty_matches_the_spec(self) -> None:
        """§6.7: -15 contraction against, -20 opposing spike."""
        result = volume_factor_score(
            VolumeFactorEvidence(
                contraction_against_claim=True,
                opposing_spike=True,
            )
        )

        assert result.score == Decimal(15)

    def test_suspect_volume_cannot_be_out_earned(self) -> None:
        """§6.6's whole purpose: corrupt volume cannot buy its way past neutral.

        The cap is applied after the sum and only downward, so stacking
        legitimate-looking bonuses on a wash-risk symbol changes nothing.
        """
        result = volume_factor_score(
            VolumeFactorEvidence(
                spike_aligned=True,
                institutional_volume=True,
                expansion_aligned=True,
                integrity_suspect=True,
            )
        )

        assert result.score == Decimal(50)
        assert result.integrity_capped is True

    def test_the_cap_never_lifts_a_low_score(self) -> None:
        """It is a cap, not a floor.

        Base 50 minus the 20-point opposing spike is 30, and a suspect symbol
        scoring 30 stays at 30 -- the integrity cap only ever removes points.
        """
        result = volume_factor_score(
            VolumeFactorEvidence(opposing_spike=True, integrity_suspect=True)
        )

        assert result.score == Decimal(30)
        assert result.integrity_capped is False

    def test_every_adjustment_carries_its_evidence_id(self) -> None:
        """§6.7: "every adjustment stores its evidence id -- the score is an
        auditable sum, not a number".
        """
        result = volume_factor_score(
            VolumeFactorEvidence(
                spike_aligned=True,
                evidence_ids={"spike_aligned": "evt-spike"},
            )
        )

        by_code = {c.code: c for c in result.contributions}

        assert by_code["spike_aligned"].evidence_id == "evt-spike"
        assert by_code["base"].points == Decimal(50)

    def test_the_score_is_the_sum_of_its_contributions(self) -> None:
        result = volume_factor_score(
            VolumeFactorEvidence(spike_aligned=True, contraction_against_claim=True)
        )

        assert result.score == sum(c.points for c in result.contributions)
