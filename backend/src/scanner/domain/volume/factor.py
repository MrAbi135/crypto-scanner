"""Volume Factor Score (SLS §6.7) — the volume engine's published output.

§6.7 gives the arithmetic outright: base 50, four bonuses, two penalties, a
clamp, and a hard cap when integrity is in doubt. It is the one factor in §8.3
that arrives fully specified, so it is transcribed rather than designed.

"Every adjustment stores its evidence id — the score is an auditable sum, not a
number." So contributions are returned, not just the total.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

BASE = Decimal(50)

SPIKE_ALIGNED = Decimal(15)
INSTITUTIONAL = Decimal(15)
EXPANSION_ALIGNED = Decimal(10)
STEALTH_FLOW = Decimal(5)

CONTRACTION_AGAINST = Decimal(-15)
OPPOSING_SPIKE = Decimal(-20)

# §6.7: a hard cap, not a penalty. Applied after the sum, so corrupt inputs
# cannot be out-earned by stacking clean ones.
INTEGRITY_CAP = Decimal(50)

FLOOR = Decimal(0)
CEILING = Decimal(100)


@dataclass(frozen=True, slots=True)
class VolumeContribution:
    code: str
    points: Decimal
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class VolumeFactor:
    score: Decimal
    contributions: tuple[VolumeContribution, ...] = field(default_factory=tuple)
    integrity_capped: bool = False


@dataclass(frozen=True, slots=True)
class VolumeFactorEvidence:
    """The §6.7 inputs, each already decided by §6.1-§6.6."""

    spike_aligned: bool = False
    institutional_volume: bool = False
    expansion_aligned: bool = False
    stealth_flow: bool = False
    contraction_against_claim: bool = False
    opposing_spike: bool = False

    # §6.6 wash_risk or §6.4 suspect_volume.
    integrity_suspect: bool = False

    evidence_ids: dict[str, str] = field(default_factory=dict)


def volume_factor_score(evidence: VolumeFactorEvidence) -> VolumeFactor:
    """§6.7, term by term."""
    contributions: list[VolumeContribution] = [
        VolumeContribution("base", BASE),
    ]

    def add(flag: bool, code: str, points: Decimal) -> None:
        if flag:
            contributions.append(VolumeContribution(code, points, evidence.evidence_ids.get(code)))

    add(evidence.spike_aligned, "spike_aligned", SPIKE_ALIGNED)
    add(evidence.institutional_volume, "institutional_volume", INSTITUTIONAL)
    add(evidence.expansion_aligned, "expansion_aligned", EXPANSION_ALIGNED)
    add(evidence.stealth_flow, "stealth_flow", STEALTH_FLOW)
    add(evidence.contraction_against_claim, "contraction_against", CONTRACTION_AGAINST)
    add(evidence.opposing_spike, "opposing_spike", OPPOSING_SPIKE)

    total = sum((c.points for c in contributions), Decimal(0))

    clamped = max(FLOOR, min(CEILING, total))

    # The cap applies last and only downward. §6.6's whole purpose is that a
    # symbol with corrupt volume cannot buy its way past neutral, so a stack of
    # legitimate-looking bonuses must not lift it.
    capped = evidence.integrity_suspect and clamped > INTEGRITY_CAP

    return VolumeFactor(
        score=INTEGRITY_CAP if capped else clamped,
        contributions=tuple(contributions),
        integrity_capped=capped,
    )


def sum_contributions(contributions: Sequence[VolumeContribution]) -> Decimal:
    return sum((c.points for c in contributions), Decimal(0))
