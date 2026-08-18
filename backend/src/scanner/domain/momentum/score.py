"""Momentum score (SLS §7.1) — one auditable 0-100 energy reading per candle.

Four sub-components of 25 each, summed. The score is a sum of named parts and
every part is returned alongside it: §7.1 calls it *auditable*, and a bare
number nobody can decompose is not.

Everything is ATR-normalised so a reading on BTC means the same thing as one on
a mid-cap — that is the whole reason §7 opens by saying so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr
from scanner.domain.common.rvol import relative_volume

WINDOW = 10
ROC_PERIOD = 10
WARMUP_CANDLES = 30

# §7.1 component ceilings.
COMPONENT_MAX = Decimal(25)

ROC_FULL = Decimal("2.5")
CONSISTENCY_FULL = 8
CONSISTENCY_FLOOR = 5
BODY_RATIO_FULL = Decimal(2)
PARTICIPATION_FULL = Decimal("1.5")

# §7.1 edge case: a 5/5 split with no directional pressure must not be dressed
# up as a reading.
NEUTRAL_ROC_CEILING = Decimal("0.5")
NEUTRAL_SCORE_CAP = Decimal(35)

# √10, to the derived precision the SLS records at (§0.4).
_SQRT_ROC_PERIOD = Decimal("3.1622776601683793")


class MomentumDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class MomentumScore:
    """The score and the four parts it is the sum of."""

    index: int
    score: Decimal
    direction: MomentumDirection
    roc: Decimal
    roc_component: Decimal
    consistency_component: Decimal
    body_component: Decimal
    participation_component: Decimal
    neutral_capped: bool


def _linear(value: Decimal, full: Decimal) -> Decimal:
    """Scale to 0-25, saturating at `full`. Never negative."""
    if value <= 0:
        return Decimal(0)

    if value >= full:
        return COMPONENT_MAX

    return COMPONENT_MAX * value / full


def directional_roc(
    candles: Sequence[Candle],
    index: int,
) -> Decimal | None:
    """`(Cl[i] - Cl[i-n]) / (ATR * sqrt(n))` for n = 10, per SLS 7.1.

    ATR-normalised and period-normalised, so the same number means the same
    energy on a slow weekly bar and a fast five-minute one.
    """
    if index < ROC_PERIOD:
        return None

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return None

    change = candles[index].close - candles[index - ROC_PERIOD].close

    return change / (atr * _SQRT_ROC_PERIOD)


def momentum_score(
    candles: Sequence[Candle],
    index: int,
) -> MomentumScore | None:
    """§7.1. Returns None until the 30-candle warm-up is satisfied.

    None rather than a low score: an unwarmed context has no reading, and a
    small number would be indistinguishable from genuinely flat energy.
    """
    if index < WARMUP_CANDLES - 1 or index >= len(candles):
        return None

    roc = directional_roc(candles, index)

    if roc is None:
        return None

    window = candles[index - WINDOW + 1 : index + 1]

    ups = [candle for candle in window if candle.close > candle.open]
    downs = [candle for candle in window if candle.close < candle.open]

    if len(ups) > len(downs):
        direction = MomentumDirection.UP
        dominant, counter = ups, downs
    elif len(downs) > len(ups):
        direction = MomentumDirection.DOWN
        dominant, counter = downs, ups
    else:
        direction = MomentumDirection.NEUTRAL
        dominant, counter = [], []

    roc_component = _linear(abs(roc), ROC_FULL)

    # Consistency: 8+ of 10 in the dominant direction is full marks, scaling
    # from 5. Below 5 there is no dominant direction to be consistent with.
    consistency = len(dominant)

    if consistency >= CONSISTENCY_FULL:
        consistency_component = COMPONENT_MAX
    elif consistency <= CONSISTENCY_FLOOR:
        consistency_component = Decimal(0)
    else:
        consistency_component = COMPONENT_MAX * (
            Decimal(consistency - CONSISTENCY_FLOOR) / Decimal(CONSISTENCY_FULL - CONSISTENCY_FLOOR)
        )

    body_component = _linear(
        _body_ratio(dominant, counter),
        BODY_RATIO_FULL,
    )

    participation_component = _linear(
        _participation_ratio(candles, index, dominant, counter),
        PARTICIPATION_FULL,
    )

    score = roc_component + consistency_component + body_component + participation_component

    # §7.1 edge case: a 5/5 split with |roc| < 0.5 is noise. The engine must not
    # manufacture direction from it, and must not report high energy either.
    neutral_capped = direction is MomentumDirection.NEUTRAL and abs(roc) < NEUTRAL_ROC_CEILING

    if neutral_capped:
        score = min(score, NEUTRAL_SCORE_CAP)

    return MomentumScore(
        index=index,
        score=score,
        direction=direction,
        roc=roc,
        roc_component=roc_component,
        consistency_component=consistency_component,
        body_component=body_component,
        participation_component=participation_component,
        neutral_capped=neutral_capped,
    )


def _body_ratio(
    dominant: Sequence[Candle],
    counter: Sequence[Candle],
) -> Decimal:
    """Mean body/range of dominant candles against counter candles.

    With no counter candles the dominant side is unopposed, which is the
    strongest form of this evidence -- so it saturates rather than dividing by
    zero. With no dominant candles there is nothing to measure.
    """
    if not dominant:
        return Decimal(0)

    dominant_mean = _mean_body_fraction(dominant)

    if not counter:
        return BODY_RATIO_FULL

    counter_mean = _mean_body_fraction(counter)

    if counter_mean <= 0:
        return BODY_RATIO_FULL

    return dominant_mean / counter_mean


def _mean_body_fraction(candles: Sequence[Candle]) -> Decimal:
    fractions = []

    for candle in candles:
        span = candle.high - candle.low

        # A candle with no range is a flat print, not a full body. Treating
        # 0/0 as 1 would score a halt as maximum conviction.
        fractions.append(abs(candle.close - candle.open) / span if span > 0 else Decimal(0))

    if not fractions:
        return Decimal(0)

    return sum(fractions, Decimal(0)) / len(fractions)


def _participation_ratio(
    candles: Sequence[Candle],
    index: int,
    dominant: Sequence[Candle],
    counter: Sequence[Candle],
) -> Decimal:
    """Mean RVOL of dominant candles over mean RVOL of counter candles.

    Returns 0 when RVOL is unavailable for the window rather than assuming
    parity: an unwarmed volume baseline is an unknown, and scoring it as 1.0
    would hand out participation marks nobody earned.
    """
    if not dominant:
        return Decimal(0)

    start = index - WINDOW + 1

    def mean_rvol(subset: Sequence[Candle]) -> Decimal | None:
        values = []

        for offset, candle in enumerate(candles[start : index + 1]):
            if candle not in subset:
                continue

            value = relative_volume(candles, start + offset)

            if value is None:
                return None

            values.append(value)

        if not values:
            return None

        return sum(values, Decimal(0)) / len(values)

    dominant_mean = mean_rvol(dominant)

    if dominant_mean is None:
        return Decimal(0)

    if not counter:
        return PARTICIPATION_FULL

    counter_mean = mean_rvol(counter)

    if counter_mean is None or counter_mean <= 0:
        return PARTICIPATION_FULL

    return dominant_mean / counter_mean
