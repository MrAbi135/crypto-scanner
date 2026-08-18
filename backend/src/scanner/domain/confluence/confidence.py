"""Base and final confidence (SLS §8.4, §8.5).

Pure arithmetic over factor scores, deliberately. The factors themselves are
computed by the engines in §3-§7, which are siblings under the acyclicity
contract; taking them as numbers keeps this layer free to be the one place the
combination rule lives.

Every adjustment is returned itemised. §8.5 requires them "itemized in the
evidence record", and a total nobody can decompose is not auditable — the same
reason §7.1's score carries its four components.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from scanner.domain.confluence.weights import WEIGHTS, Factor, Grade, grade

# §8.5 caps. Bounded on purpose: adjustments refine a score, they never rescue
# one. A setup that needs +40 of synergy to reach a floor did not earn the floor.
MAX_SYNERGY = Decimal(15)
MAX_PENALTY = Decimal(20)

FACTOR_MIN = Decimal(0)
FACTOR_MAX = Decimal(100)


@dataclass(frozen=True, slots=True)
class Adjustment:
    """One named bonus or penalty. Positive is synergy, negative is conflict."""

    code: str
    points: Decimal
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class Confidence:
    base: Decimal
    synergy: Decimal
    penalty: Decimal
    final: Decimal
    published_grade: Grade | None
    applied: tuple[Adjustment, ...] = field(default_factory=tuple)
    synergy_capped: bool = False
    penalty_capped: bool = False


def base_confidence(factors: Mapping[Factor, Decimal]) -> Decimal:
    """§8.4: `sum(F_k * W_k)`.

    Every factor must be supplied. A missing factor is not a zero — §8.3 says
    each score is "a sum of enumerated evidence contributions", so absence
    means the evidence was never gathered, and silently scoring it 0 would
    publish a confidence the engine never actually computed.
    """
    missing = sorted(f.value for f in WEIGHTS if f not in factors)

    if missing:
        raise ValueError(f"missing factor scores: {', '.join(missing)}")

    for factor, score in factors.items():
        if not FACTOR_MIN <= score <= FACTOR_MAX:
            raise ValueError(f"{factor.value} score {score} outside 0-100")

    return sum(
        (factors[factor] * weight for factor, weight in WEIGHTS.items()),
        Decimal(0),
    )


def final_confidence(
    factors: Mapping[Factor, Decimal],
    adjustments: Sequence[Adjustment] = (),
) -> Confidence:
    """§8.5: `clamp(base + bonuses - penalties, 0, 100)`, floored to an int.

    The floor is the spec's own arithmetic: §8.7 carries 95.25 to a published
    **95**. Rounding up would let a setup reach a grade band it did not make.
    """
    base = base_confidence(factors)

    raw_synergy = sum(
        (a.points for a in adjustments if a.points > 0),
        Decimal(0),
    )

    raw_penalty = sum(
        (-a.points for a in adjustments if a.points < 0),
        Decimal(0),
    )

    synergy = min(raw_synergy, MAX_SYNERGY)
    penalty = min(raw_penalty, MAX_PENALTY)

    unclamped = base + synergy - penalty

    final = max(FACTOR_MIN, min(FACTOR_MAX, unclamped))

    # Floored, not rounded, and to a whole number: §9.4's bands are integers
    # and a half-point should never decide a grade.
    final = final.quantize(Decimal(1), rounding=ROUND_FLOOR)

    return Confidence(
        base=base,
        synergy=synergy,
        penalty=penalty,
        final=final,
        published_grade=grade(final),
        applied=tuple(adjustments),
        synergy_capped=raw_synergy > MAX_SYNERGY,
        penalty_capped=raw_penalty > MAX_PENALTY,
    )
