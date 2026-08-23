"""Shared non-repainting swing engine (SLS §3.1)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.structure.model import (
    SwingKind,
    SwingPoint,
    SwingStrength,
)

_INTERNAL_K = 2
_EXTERNAL_K = 5


def swing_window(
    strength: SwingStrength,
) -> int:
    """Return the configured half-window k for one swing strength."""

    if strength is SwingStrength.INTERNAL:
        return _INTERNAL_K

    if strength is SwingStrength.EXTERNAL:
        return _EXTERNAL_K

    raise ValueError(f"unsupported swing strength: {strength}")


def detect_swings(
    candles: Sequence[Candle],
    *,
    strength: SwingStrength,
    epsilon: Decimal = Decimal("0"),
) -> tuple[SwingPoint, ...]:
    """Detect confirmed swings from an append-only closed-candle series.

    A candidate needs k candles on both sides, therefore only confirmed
    historical facts are emitted. Equal extremes resolve deterministically
    to the last candle of the equal set that is followed by k strictly
    lower highs / higher lows.
    """

    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")

    k = swing_window(strength)

    if len(candles) < (2 * k) + 1:
        return ()

    swings: list[SwingPoint] = []

    for index in range(
        k,
        len(candles) - k,
    ):
        if _is_swing_high(
            candles,
            index=index,
            k=k,
            epsilon=epsilon,
        ):
            swings.append(
                SwingPoint(
                    index=index,
                    open_time=candles[index].open_time,
                    price=candles[index].high,
                    kind=SwingKind.HIGH,
                    strength=strength,
                )
            )

        if _is_swing_low(
            candles,
            index=index,
            k=k,
            epsilon=epsilon,
        ):
            swings.append(
                SwingPoint(
                    index=index,
                    open_time=candles[index].open_time,
                    price=candles[index].low,
                    kind=SwingKind.LOW,
                    strength=strength,
                )
            )

    swings.sort(
        key=lambda swing: (
            swing.index,
            swing.kind.value,
        )
    )

    return tuple(swings)


def detect_internal_swings(
    candles: Sequence[Candle],
    *,
    epsilon: Decimal = Decimal("0"),
) -> tuple[SwingPoint, ...]:
    return detect_swings(
        candles,
        strength=SwingStrength.INTERNAL,
        epsilon=epsilon,
    )


def detect_external_swings(
    candles: Sequence[Candle],
    *,
    epsilon: Decimal = Decimal("0"),
) -> tuple[SwingPoint, ...]:
    return detect_swings(
        candles,
        strength=SwingStrength.EXTERNAL,
        epsilon=epsilon,
    )


def _has_lower_left(
    candles: Sequence[Candle],
    *,
    index: int,
    candidate: Decimal,
    epsilon: Decimal,
) -> bool:
    """Is anything materially lower to the left, looking past the equal run?

    Walks back over candles whose high matches the candidate within epsilon --
    the equal set the candidate terminates -- and asks whether the candle
    before the set is lower. A run that reaches the start of the series has no
    such candle, which is how a flat shelf is still refused.
    """
    cursor = index - 1

    while cursor >= 0 and candles[cursor].high >= candidate - epsilon:
        cursor -= 1

    return cursor >= 0


def _has_higher_left(
    candles: Sequence[Candle],
    *,
    index: int,
    candidate: Decimal,
    epsilon: Decimal,
) -> bool:
    """Mirror of `_has_lower_left` on lows."""
    cursor = index - 1

    while cursor >= 0 and candles[cursor].low <= candidate + epsilon:
        cursor -= 1

    return cursor >= 0


def _is_swing_high(
    candles: Sequence[Candle],
    *,
    index: int,
    k: int,
    epsilon: Decimal,
) -> bool:
    candidate = candles[index].high

    left = candles[index - k : index]
    right = candles[index + 1 : index + k + 1]

    # Nothing in the complete window may be materially higher.
    if any(candle.high > candidate + epsilon for candle in (*left, *right)):
        return False

    # Confirmation requires k strictly lower highs on the right.
    # Therefore earlier candles of an equal-high set cannot confirm;
    # only the final equal extreme can.
    if any(candle.high >= candidate - epsilon for candle in right):
        return False

    # Something must be materially lower to the left, or a flat shelf would
    # read as a peak. Looked for past the equal set rather than inside the
    # window: an equal run is one extreme, and with `k` narrow enough the run
    # can fill the whole left window, leaving nothing lower to find. That is
    # what broke §3.1's "every external swing is by construction also an
    # internal one" -- a three-candle equal run made the pivot visible at
    # k_ext = 5, which sees past it, and invisible at k_int = 2, which does
    # not.
    return _has_lower_left(candles, index=index, candidate=candidate, epsilon=epsilon)


def _is_swing_low(
    candles: Sequence[Candle],
    *,
    index: int,
    k: int,
    epsilon: Decimal,
) -> bool:
    candidate = candles[index].low

    left = candles[index - k : index]
    right = candles[index + 1 : index + k + 1]

    if any(candle.low < candidate - epsilon for candle in (*left, *right)):
        return False

    # Mirror of equal-high handling: k strictly higher lows must follow.
    if any(candle.low <= candidate + epsilon for candle in right):
        return False

    # Mirror of the equal-high case; see `_has_lower_left`.
    return _has_higher_left(candles, index=index, candidate=candidate, epsilon=epsilon)
