"""Evidence factors (SLS §8.3) — the framework, and the two that are specified.

§8.3 requires each factor to be "a sum of enumerated evidence contributions
with stored event ids — reproducible from the evidence alone". That shape is
built here for all six.

**Only F4 and F6 have their point values in the spec.** §8.3 says what earns
points for F1, F2, F3 and F5 — displaced beats plain, external beats internal,
FRESH beats TESTED — but never how many. Appendix A carries `P.rank.weights`
and no factor point table, and §8.7's worked example supplies F1=85, F2=80,
F3=90, F5=65 as *given inputs* rather than deriving them.

So those four are not implemented. Inventing a table here would make my
arithmetic the doctrine: "reproducible from the evidence alone" would mean
reproducible from numbers nobody ratified, and §8.7's own example could never
be re-derived because there is nothing to re-derive it from. The gap needs an
SLS amendment, which is a doctrine decision.

F4 is `§6.7`'s published score, unmodified. F6 has an exact four-value table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from scanner.domain.confluence.weights import Factor

FACTOR_FLOOR = Decimal(0)
FACTOR_CEILING = Decimal(100)

# §8.3 F6, verbatim: aligned=100, HTF CAUTION toward D=70, RANGING=50,
# opposed=0. The opposed case is not a low score with a chance of recovery --
# §8.6 permits it only inside a Sweep Reversal, enforced there rather than here.
HTF_ALIGNED = Decimal(100)
HTF_CAUTION_TOWARD = Decimal(70)
HTF_RANGING = Decimal(50)
HTF_OPPOSED = Decimal(0)


@dataclass(frozen=True, slots=True)
class Contribution:
    """One enumerated piece of evidence and what it was worth."""

    code: str
    points: Decimal
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class FactorScore:
    factor: Factor
    score: Decimal
    contributions: tuple[Contribution, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not FACTOR_FLOOR <= self.score <= FACTOR_CEILING:
            raise ValueError(f"{self.factor.value} score {self.score} outside 0-100")


def from_contributions(
    factor: Factor,
    contributions: tuple[Contribution, ...],
) -> FactorScore:
    """Sum, then clamp. The contributions survive the clamp deliberately.

    A factor that would have scored 130 before clamping is different evidence
    from one that scored exactly 100, and §8.3 wants the record either way.
    """
    total = sum((c.points for c in contributions), Decimal(0))

    return FactorScore(
        factor=factor,
        score=max(FACTOR_FLOOR, min(FACTOR_CEILING, total)),
        contributions=contributions,
    )


def volume_factor(score: Decimal, evidence_id: str | None = None) -> FactorScore:
    """F4 — §6.7's Volume Factor Score, passed through unmodified.

    §8.3 says "as published". Re-deriving or adjusting it here would give the
    platform two volume scores that could disagree.
    """
    return FactorScore(
        factor=Factor.VOLUME,
        score=score,
        contributions=(Contribution("volume_factor_score", score, evidence_id),),
    )


def htf_alignment_factor(
    *,
    htf_state: str,
    direction: str,
    evidence_id: str | None = None,
) -> FactorScore:
    """F6 — §8.3's four-value table over the §3.7 HTF bias.

    `htf_state` is the next TF up's trend state: UP, DOWN, RANGING or CAUTION.
    `direction` is the candidate's D: UP or DOWN.
    """
    if direction not in {"UP", "DOWN"}:
        raise ValueError(f"direction must be UP or DOWN, got {direction!r}")

    if htf_state == direction:
        points, code = HTF_ALIGNED, "htf_aligned"
    elif htf_state == "RANGING":
        points, code = HTF_RANGING, "htf_ranging"
    elif htf_state == f"CAUTION_{direction}":
        # CAUTION carries a direction of its own: caution *toward* D is partial
        # support, caution away from it is not. Collapsing both to one
        # "CAUTION" value would score an HTF turning against the trade as
        # though it were leaning into it.
        points, code = HTF_CAUTION_TOWARD, "htf_caution_toward"
    else:
        points, code = HTF_OPPOSED, "htf_opposed"

    return FactorScore(
        factor=Factor.HTF_ALIGNMENT,
        score=points,
        contributions=(Contribution(code, points, evidence_id),),
    )
