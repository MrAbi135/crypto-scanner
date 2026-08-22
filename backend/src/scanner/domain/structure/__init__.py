"""Market-structure domain (SLS §3)."""

from scanner.domain.structure.breaks import (
    BosEvent,
    BreakDirection,
    detect_bos,
    is_wick_only_penetration,
)
from scanner.domain.structure.classification import classify_swings
from scanner.domain.structure.model import (
    ClassifiedSwing,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    unbroken_pairs,
)
from scanner.domain.structure.mss import (
    MssDecision,
    MssEvidence,
    evaluate_mss,
    mss_is_low_quality,
)
from scanner.domain.structure.swings import (
    detect_external_swings,
    detect_internal_swings,
    detect_swings,
    swing_window,
)
from scanner.domain.structure.trend import (
    TrendState,
    TrendStateMachine,
)

__all__ = [
    "BosEvent",
    "BreakDirection",
    "ClassifiedSwing",
    "MssDecision",
    "MssEvidence",
    "StructureLabel",
    "SwingKind",
    "SwingPoint",
    "SwingStrength",
    "TrendState",
    "TrendStateMachine",
    "classify_swings",
    "detect_bos",
    "detect_external_swings",
    "detect_internal_swings",
    "detect_swings",
    "evaluate_mss",
    "is_wick_only_penetration",
    "mss_is_low_quality",
    "swing_window",
    "unbroken_pairs",
]
