"""Structure-break primitives and BOS detection (SLS §3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.common import Candle
from scanner.domain.structure.model import SwingKind, SwingPoint


class BreakDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True, slots=True)
class BosEvent:
    """Confirmed Break of Structure fact."""

    direction: BreakDirection
    swing: SwingPoint
    break_price: Decimal
    candle_close: Decimal


def detect_bos(
    candle: Candle,
    swing: SwingPoint,
    *,
    direction: BreakDirection,
    epsilon: Decimal = Decimal("0"),
) -> BosEvent | None:
    """Detect a close-confirmed BOS against one confirmed swing."""

    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")

    if direction is BreakDirection.UP:
        if swing.kind is not SwingKind.HIGH:
            raise ValueError("upward BOS must reference a swing high")

        if candle.close <= swing.price + epsilon:
            return None

    else:
        if swing.kind is not SwingKind.LOW:
            raise ValueError("downward BOS must reference a swing low")

        if candle.close >= swing.price - epsilon:
            return None

    return BosEvent(
        direction=direction,
        swing=swing,
        break_price=swing.price,
        candle_close=candle.close,
    )


def is_wick_only_penetration(
    candle: Candle,
    swing: SwingPoint,
    *,
    direction: BreakDirection,
    epsilon: Decimal = Decimal("0"),
) -> bool:
    """Whether price penetrated a swing only by wick, not closing beyond it."""

    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")

    if direction is BreakDirection.UP:
        if swing.kind is not SwingKind.HIGH:
            raise ValueError("upward penetration must reference a swing high")

        return candle.high > swing.price and candle.close <= swing.price + epsilon

    if swing.kind is not SwingKind.LOW:
        raise ValueError("downward penetration must reference a swing low")

    return candle.low < swing.price and candle.close >= swing.price - epsilon
