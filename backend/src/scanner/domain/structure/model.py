"""Structure domain primitives (SLS §3, Sprint S4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingStrength(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class StructureLabel(str, Enum):
    HH = "HH"
    LH = "LH"
    EQH = "EQH"
    HL = "HL"
    LL = "LL"
    EQL = "EQL"
    # The first swing of its kind in a series has no same-kind predecessor to
    # compare against, so it carries SEED rather than a directional label
    # (SLS §3.3). SEED is deliberately excluded from every directional set —
    # it establishes a reference point, it does not assert a direction.
    SEED = "SEED"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """One confirmed, non-repainting swing fact."""

    index: int
    open_time: datetime
    price: Decimal
    kind: SwingKind
    strength: SwingStrength


@dataclass(frozen=True, slots=True)
class ClassifiedSwing:
    """Swing plus its classification against the prior same-kind swing."""

    swing: SwingPoint
    label: StructureLabel


# §7.4's "consecutive unbroken HH/HL or LL/LH pairs", by direction. EQH/EQL and
# SEED are in neither set: an equal extreme asserts no direction, and §3.3 says
# the same of SEED.
_UPTREND_LABELS = frozenset({StructureLabel.HH, StructureLabel.HL})
_DOWNTREND_LABELS = frozenset({StructureLabel.LL, StructureLabel.LH})


def unbroken_pairs(labels: Sequence[StructureLabel], direction: str) -> int:
    """§7.4: consecutive unbroken HH/HL (or LL/LH) pairs, newest-first.

    Counted from the newest swing backwards, because the term is trend
    *maturity* -- how long the current run has held, not the best run anywhere
    in the window. A single contrary label ends the count; that is what
    "unbroken" means.

    Two swings make a pair, so an odd trailing label contributes nothing on its
    own: one HH after a broken run is not yet a trend.
    """
    wanted = _UPTREND_LABELS if direction == "UP" else _DOWNTREND_LABELS

    run = 0

    for label in reversed(labels):
        if label not in wanted:
            break

        run += 1

    return run // 2
