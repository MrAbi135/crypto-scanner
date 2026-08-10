"""Structure domain primitives (SLS §3, Sprint S4)."""

from __future__ import annotations

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
