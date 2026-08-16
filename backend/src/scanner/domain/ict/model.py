"""Shared ICT zone-domain models (SLS §5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ZoneType(str, Enum):
    OB = "OB"
    BREAKER = "BREAKER"
    MITIGATION = "MITIGATION"
    FVG = "FVG"
    IFVG = "IFVG"
    BPR = "BPR"
    OTE = "OTE"


class ZonePolarity(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class ZoneState(str, Enum):
    FRESH = "FRESH"
    TESTED = "TESTED"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class FvgState(str, Enum):
    OPEN = "OPEN"
    TOUCHED = "TOUCHED"
    CE_FILLED = "CE_FILLED"
    FILLED = "FILLED"
    INVERTED = "INVERTED"
    EXPIRED = "EXPIRED"


class IfvgState(str, Enum):
    UNPROVEN = "UNPROVEN"
    FRESH = "FRESH"
    TESTED = "TESTED"
    MITIGATED = "MITIGATED"
    DEAD = "DEAD"
    EXPIRED = "EXPIRED"


class InteractionKind(str, Enum):
    TOUCH = "TOUCH"
    REJECTION = "REJECTION"
    MITIGATION = "MITIGATION"
    RESPECT = "RESPECT"
    VIOLATION = "VIOLATION"
    CONFIRMATION = "CONFIRMATION"


@dataclass(frozen=True, slots=True)
class ZoneBand:
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("zone high cannot be below zone low")

    @property
    def height(self) -> Decimal:
        return self.high - self.low

    @property
    def midpoint(self) -> Decimal:
        return self.low + self.height / Decimal("2")


@dataclass(frozen=True, slots=True)
class ZoneInteraction:
    kind: InteractionKind
    candle_index: int
    observed_at: datetime
    penetration_depth: Decimal
    close_price: Decimal
    rejection_wick: Decimal
    close_through: bool


@dataclass(frozen=True, slots=True)
class Zone:
    zone_id: str
    zone_type: ZoneType
    polarity: ZonePolarity
    band: ZoneBand
    refined_band: ZoneBand | None
    created_index: int
    created_at: datetime
    grade: str
    state: str
    stale_context: bool = False
    gap_adjacent: bool = False
    origin_swept: bool | None = None
