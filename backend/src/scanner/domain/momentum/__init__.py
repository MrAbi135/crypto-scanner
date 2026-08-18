"""Momentum engine (SLS §7).

Momentum answers *when*: is energy building, releasing, or fading. Every measure
is ATR-normalised so a reading means the same thing across symbols.

**Here:** the momentum score (§7.1), acceleration and the exhaustion tag (§7.2),
range expansion and compression (§7.3).

Legs (§7.5) and trend strength (§7.4) are here too. Legs need displacement,
which is §5.10 in `domain/ict` — a sibling this package may not import — so
they take displacement as a set of candle indices, the same dodge
`detect_stop_hunt` uses. The application layer knows both engines and supplies
the crossing fact.
"""

from scanner.domain.momentum.legs import (
    IMPULSE_MIN_ATR,
    MICRO_MAX_ATR,
    Leg,
    LegKind,
    anchoring_legs,
    segment_legs,
)
from scanner.domain.momentum.phases import (
    ACCEL_THRESHOLD,
    COMPRESSION_WINDOW,
    MomentumPhase,
    detect_compression,
    detect_range_expansion,
    momentum_phase,
)
from scanner.domain.momentum.score import (
    WARMUP_CANDLES,
    WINDOW,
    MomentumDirection,
    MomentumScore,
    directional_roc,
    momentum_score,
)
from scanner.domain.momentum.trend_strength import (
    ALIGNMENT_MAX,
    OTE_DEEP,
    OTE_SHALLOW,
    PULLBACK_MAX,
    STRUCTURAL_MAX,
    TrendStrength,
    trend_strength,
)

__all__ = [
    "ACCEL_THRESHOLD",
    "ALIGNMENT_MAX",
    "COMPRESSION_WINDOW",
    "IMPULSE_MIN_ATR",
    "MICRO_MAX_ATR",
    "OTE_DEEP",
    "OTE_SHALLOW",
    "PULLBACK_MAX",
    "STRUCTURAL_MAX",
    "WARMUP_CANDLES",
    "WINDOW",
    "Leg",
    "LegKind",
    "MomentumDirection",
    "MomentumPhase",
    "MomentumScore",
    "TrendStrength",
    "anchoring_legs",
    "detect_compression",
    "detect_range_expansion",
    "directional_roc",
    "momentum_phase",
    "momentum_score",
    "segment_legs",
    "trend_strength",
]
