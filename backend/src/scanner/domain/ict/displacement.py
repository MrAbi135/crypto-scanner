"""ICT displacement primitive (SLS §5.10)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.common import Candle

_BODY_MULT = Decimal("2.0")
_RANGE_MULT = Decimal("1.5")
_CLOSE_PCT = Decimal("0.25")
_LOOKBACK = 20
_ZERO = Decimal("0")


class DisplacementDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True, slots=True)
class Displacement:
    candle_index: int
    direction: DisplacementDirection
    body: Decimal
    candle_range: Decimal
    mean_body_20: Decimal
    atr: Decimal
    body_multiple: Decimal
    range_multiple: Decimal
    close_position: Decimal


def detect_displacement(
    candles: Sequence[Candle],
    index: int,
    *,
    atr: Decimal,
) -> Displacement | None:
    """Return displacement evidence when candle ``index`` satisfies SLS §5.10."""

    if index < 0 or index >= len(candles):
        raise IndexError("candle index out of range")

    if index < _LOOKBACK:
        return None

    if atr <= _ZERO:
        return None

    candle = candles[index]

    body_signed = candle.close - candle.open

    if body_signed == _ZERO:
        return None

    body = abs(body_signed)
    candle_range = candle.high - candle.low

    if candle_range <= _ZERO:
        return None

    lookback = candles[index - _LOOKBACK : index]

    mean_body_20 = sum(
        (abs(previous.close - previous.open) for previous in lookback),
        _ZERO,
    ) / Decimal(_LOOKBACK)

    if mean_body_20 <= _ZERO:
        return None

    body_multiple = body / mean_body_20
    range_multiple = candle_range / atr

    if body_signed > _ZERO:
        direction = DisplacementDirection.BULLISH
        close_position = (candle.high - candle.close) / candle_range
    else:
        direction = DisplacementDirection.BEARISH
        close_position = (candle.close - candle.low) / candle_range

    if body_multiple < _BODY_MULT:
        return None

    if range_multiple < _RANGE_MULT:
        return None

    if close_position > _CLOSE_PCT:
        return None

    return Displacement(
        candle_index=index,
        direction=direction,
        body=body,
        candle_range=candle_range,
        mean_body_20=mean_body_20,
        atr=atr,
        body_multiple=body_multiple,
        range_multiple=range_multiple,
        close_position=close_position,
    )
