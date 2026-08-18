"""Confluence engine (SLS §8) and the ranking arithmetic it feeds (§9).

**Here:** the hard-gate battery (§8.2), weighted base confidence (§8.4),
bounded adjustments and final confidence (§8.5), factor weights (§9.1) and
grade bands (§9.4).

**Not here.** Factor *scoring* (§8.3) reads structure, liquidity, zone, volume
and momentum evidence -- five sibling packages this one may not import -- so it
composes in the application layer. Archetype classification (§8.6) needs the
same evidence plus leg data from §7.5, which does not exist yet. Cross-symbol
ranking (§9.2) operates over published setups rather than a single candidate.

What is here is the part that is pure arithmetic over supplied numbers, which
is also the part §8.7 pins with a normative worked example.
"""

from scanner.domain.confluence.confidence import (
    FACTOR_MAX,
    FACTOR_MIN,
    MAX_PENALTY,
    MAX_SYNERGY,
    Adjustment,
    Confidence,
    base_confidence,
    final_confidence,
)
from scanner.domain.confluence.gates import (
    Gate,
    GateEvidence,
    GateResult,
    evaluate_gates,
)
from scanner.domain.confluence.weights import (
    GRADE_A_FLOOR,
    GRADE_B_FLOOR,
    GRADE_S_FLOOR,
    WEIGHTS,
    Factor,
    Grade,
    grade,
)

__all__ = [
    "FACTOR_MAX",
    "FACTOR_MIN",
    "GRADE_A_FLOOR",
    "GRADE_B_FLOOR",
    "GRADE_S_FLOOR",
    "MAX_PENALTY",
    "MAX_SYNERGY",
    "WEIGHTS",
    "Adjustment",
    "Confidence",
    "Factor",
    "Gate",
    "GateEvidence",
    "GateResult",
    "Grade",
    "base_confidence",
    "evaluate_gates",
    "final_confidence",
    "grade",
]
