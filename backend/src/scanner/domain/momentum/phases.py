"""Acceleration and range phases (SLS §7.2, §7.3).

Both read the momentum score or ATR and answer "what phase is this", so neither
owns state: they recompute per close like §6.3's context flags.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr
from scanner.domain.momentum.score import momentum_score

# §7.2
ACCEL_LOOKBACK = 3
ACCEL_THRESHOLD = Decimal(10)
ACCEL_MIN_SCORE = Decimal(50)
EXHAUSTION_PROGRESS_ATR = Decimal("0.5")

# §7.3
EXPANSION_WINDOW = 3
EXPANSION_MEAN_RANGE_ATR = Decimal("1.4")
COMPRESSION_WINDOW = 7
COMPRESSION_RANGE_ATR = Decimal("0.7")
COMPRESSION_ENVELOPE_ATR = Decimal(2)


@dataclass(frozen=True, slots=True)
class MomentumPhase:
    index: int
    accel: Decimal
    accelerating: bool
    decelerating: bool
    exhaustion_watch: bool


def momentum_phase(
    candles: Sequence[Candle],
    index: int,
) -> MomentumPhase | None:
    """§7.2. Three-candle score differential, plus the exhaustion tag."""
    now = momentum_score(candles, index)
    then = momentum_score(candles, index - ACCEL_LOOKBACK)

    if now is None or then is None:
        return None

    accel = now.score - then.score

    # Accelerating needs both a rise and a real level: a jump from 5 to 20 is a
    # bigger differential than 60 to 70 and means far less.
    accelerating = accel >= ACCEL_THRESHOLD and now.score >= ACCEL_MIN_SCORE

    decelerating = accel <= -ACCEL_THRESHOLD

    return MomentumPhase(
        index=index,
        accel=accel,
        accelerating=accelerating,
        decelerating=decelerating,
        # Fading energy while price still grinds out marginal extremes is the
        # tired-trend signature §7.2 wants flagged -- prime context for a sweep.
        exhaustion_watch=decelerating and _marginal_progress(candles, index),
    )


def _marginal_progress(candles: Sequence[Candle], index: int) -> bool:
    """Less than 0.5 x ATR of progress per candle over the differential window."""
    if index < ACCEL_LOOKBACK:
        return False

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return False

    progress = abs(candles[index].close - candles[index - ACCEL_LOOKBACK].close)

    return progress < EXHAUSTION_PROGRESS_ATR * atr * ACCEL_LOOKBACK


def detect_range_expansion(candles: Sequence[Candle], index: int) -> bool:
    """§7.3: three-candle mean range >= 1.4 x ATR."""
    if index < EXPANSION_WINDOW - 1:
        return False

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return False

    window = candles[index - EXPANSION_WINDOW + 1 : index + 1]

    mean_range = sum((c.high - c.low for c in window), Decimal(0)) / EXPANSION_WINDOW

    return mean_range >= EXPANSION_MEAN_RANGE_ATR * atr


def detect_compression(candles: Sequence[Candle], index: int) -> bool:
    """§7.3 NR7-style coil: seven tight candles inside a tight envelope.

    Both conditions, and the envelope is the one that matters. Seven small
    candles walking steadily downhill each satisfy the per-candle test while
    covering enormous ground -- that is a trend, not a coil, and only the
    envelope test tells them apart.
    """
    if index < COMPRESSION_WINDOW - 1:
        return False

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return False

    window = candles[index - COMPRESSION_WINDOW + 1 : index + 1]

    if any(c.high - c.low > COMPRESSION_RANGE_ATR * atr for c in window):
        return False

    envelope = max(c.high for c in window) - min(c.low for c in window)

    return envelope <= COMPRESSION_ENVELOPE_ATR * atr
