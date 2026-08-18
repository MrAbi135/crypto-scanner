"""Confluence engine (SLS §8) and the ranking arithmetic it feeds (§9).

**Here:** the hard-gate battery (§8.2), weighted base confidence (§8.4),
bounded adjustments and final confidence (§8.5), factor weights (§9.1) and
grade bands (§9.4).

Archetype classification (§8.6) evaluates supplied chain facts, the same shape
as the gate battery: the application layer establishes them by reading across
five sibling engines, and this layer decides what they add up to.

Factor scoring (§8.3) provides the contribution framework for all six factors
and implements the two the spec actually prices: F4 passes through §6.7's
published score, F6 applies §8.3's four-value HTF table, and F1, F2, F3 and F5
use the point tables added by **SLS v1.0.5 §8.3.1** — values the spec did not
carry until that amendment, fitted to §8.7's own worked example rather than to
preference.

Cross-symbol ranking (§9.2) operates over
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
from scanner.domain.confluence.factor_points import (
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
from scanner.domain.confluence.factors import (
    FACTOR_CEILING,
    FACTOR_FLOOR,
    HTF_ALIGNED,
    HTF_CAUTION_TOWARD,
    HTF_OPPOSED,
    HTF_RANGING,
    Contribution,
    FactorScore,
    from_contributions,
    htf_alignment_factor,
    volume_factor,
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
    "FACTOR_CEILING",
    "FACTOR_FLOOR",
    "FACTOR_MAX",
    "FACTOR_MIN",
    "FLOORS",
    "GRADE_A_FLOOR",
    "GRADE_B_FLOOR",
    "GRADE_S_FLOOR",
    "HTF_ALIGNED",
    "HTF_CAUTION_TOWARD",
    "HTF_OPPOSED",
    "HTF_RANGING",
    "MAX_FVG_AGE_CANDLES",
    "MAX_PENALTY",
    "MAX_SYNERGY",
    "RANGE_MIN_ATR",
    "RANKING_PRIORITY",
    "WEIGHTS",
    "ZONE_GRADE_POINTS",
    "ZONE_STATE_POINTS",
    "Adjustment",
    "Archetype",
    "ArchetypeEvidence",
    "Confidence",
    "Contribution",
    "Factor",
    "FactorScore",
    "Gate",
    "GateEvidence",
    "GateResult",
    "Grade",
    "LiquidityEvidence",
    "MomentumEvidence",
    "StructureEvidence",
    "ZoneEvidence",
    "base_confidence",
    "classify_archetype",
    "evaluate_gates",
    "final_confidence",
    "from_contributions",
    "grade",
    "htf_alignment_factor",
    "liquidity_factor",
    "meets_floor",
    "momentum_factor",
    "ranking_priority",
    "structure_factor",
    "volume_factor",
    "zone_factor",
]
