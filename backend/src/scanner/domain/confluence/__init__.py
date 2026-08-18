"""Confluence engine (SLS §8) and the ranking arithmetic it feeds (§9).

**Here:** the hard-gate battery (§8.2), weighted base confidence (§8.4),
bounded adjustments and final confidence (§8.5), factor weights (§9.1) and
grade bands (§9.4).

Archetype classification (§8.6) evaluates supplied chain facts, the same shape
as the gate battery: the application layer establishes them by reading across
five sibling engines, and this layer decides what they add up to.

**Not here.** Factor *scoring* (§8.3) reads those five engines directly, so it
composes in the application layer. Cross-symbol ranking (§9.2) operates over
published setups rather than a single candidate — only its archetype tie-break
order lives here, next to the classification order it is often confused with.

What is here is the part that is pure arithmetic over supplied numbers, which
is also the part §8.7 pins with a normative worked example.
"""

from scanner.domain.confluence.archetypes import (
    CLASSIFICATION_ORDER,
    FLOORS,
    MAX_FVG_AGE_CANDLES,
    RANGE_MIN_ATR,
    RANKING_PRIORITY,
    Archetype,
    ArchetypeEvidence,
    classify_archetype,
    meets_floor,
    ranking_priority,
)
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
    "CLASSIFICATION_ORDER",
    "FACTOR_MAX",
    "FACTOR_MIN",
    "FLOORS",
    "GRADE_A_FLOOR",
    "GRADE_B_FLOOR",
    "GRADE_S_FLOOR",
    "MAX_FVG_AGE_CANDLES",
    "MAX_PENALTY",
    "MAX_SYNERGY",
    "RANGE_MIN_ATR",
    "RANKING_PRIORITY",
    "WEIGHTS",
    "Adjustment",
    "Archetype",
    "ArchetypeEvidence",
    "Confidence",
    "Factor",
    "Gate",
    "GateEvidence",
    "GateResult",
    "Grade",
    "base_confidence",
    "classify_archetype",
    "evaluate_gates",
    "final_confidence",
    "grade",
    "meets_floor",
    "ranking_priority",
]
