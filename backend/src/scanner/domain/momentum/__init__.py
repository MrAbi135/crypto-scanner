"""Momentum engine (SLS §7).

Momentum answers *when*: is energy building, releasing, or fading. Every measure
is ATR-normalised so a reading means the same thing across symbols.

**Here:** the momentum score (§7.1), acceleration and the exhaustion tag (§7.2),
range expansion and compression (§7.3).

**Not here yet.** §7.4 trend strength is a composite over structure pairs and
leg retracement depth, and §7.5 legs need displacement — which lives in
`domain/ict`, a sibling under the engine-acyclicity contract that this package
may not import. Composition belongs in the application layer, the same place
the stop-hunt composite ended up for the same reason.
"""

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

__all__ = [
    "ACCEL_THRESHOLD",
    "COMPRESSION_WINDOW",
    "WARMUP_CANDLES",
    "WINDOW",
    "MomentumDirection",
    "MomentumPhase",
    "MomentumScore",
    "detect_compression",
    "detect_range_expansion",
    "directional_roc",
    "momentum_phase",
    "momentum_score",
]
