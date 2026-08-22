"""Structure-break primitives and BOS detection (SLS §3.5)."""

from __future__ import annotations

from collections.abc import Sequence
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


# §3.5: `P.structure.failed_break_candles = 3` closed candles.
FAILED_BREAK_CANDLES = 3


def failed_break_index(
    candles: Sequence[Candle],
    *,
    break_index: int,
    level: Decimal,
    direction: BreakDirection,
    within: int = FAILED_BREAK_CANDLES,
) -> int | None:
    """§3.5: the candle that closes back beyond a broken level, if one does.

    "A failed break is recorded (fact, not deletion) if within
    `P.structure.failed_break_candles = 3` closed candles price closes back
    beyond the broken level in the opposite direction."

    The first such close, not the deepest: the break has already failed by
    then, and a later one is the same fact observed twice.

    **No epsilon.** §3.5 attaches its tolerance to the break -- "break of a
    level within epsilon: not a break" -- and says nothing about the reclaim.
    The asymmetry is deliberate rather than an oversight to be tidied up:
    widening the reclaim would suppress contrary evidence, and §8.3.1 pays 15
    points for the *absence* of a failed break. A rule that errs should err
    toward recording the failure, not toward the points.
    """
    if within < 1:
        raise ValueError("within must be at least one candle")

    for index in range(break_index + 1, min(break_index + within, len(candles) - 1) + 1):
        close = candles[index].close

        if direction is BreakDirection.UP and close < level:
            return index

        if direction is BreakDirection.DOWN and close > level:
            return index

    return None
