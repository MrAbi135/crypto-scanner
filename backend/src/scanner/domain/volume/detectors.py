"""Volume spike and expansion/contraction (SLS §6.2, §6.3).

Both are per-candle measurements rather than stateful objects: §6.3 says the
context flags "recompute each close; no persistence beyond evidence recording",
and §6.2 calls its output a measurement with no invalidation. Nothing here owns
a lifecycle, which is why none of it needs a repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr
from scanner.domain.common.rvol import RvolClass, classify, relative_volume

# P.volume.spike_floor -- an absolute quote-volume floor so a micro-cap's $8k
# "spike" cannot score merely by being 5x its own tiny baseline (§6.2).
SPIKE_FLOOR_QUOTE = Decimal("250000")

# §6.2 conviction tag.
CONVICTION_DELTA = Decimal("0.25")

# §6.3 expansion.
EXPANSION_MEAN_RVOL = Decimal("1.2")
EXPANSION_PROGRESS_ATR = Decimal("0.75")

# §6.3 contraction -- both dimensions must contract.
CONTRACTION_MEAN_RVOL = Decimal("0.6")
CONTRACTION_MEAN_RANGE_ATR = Decimal("0.6")
CONTRACTION_WINDOW = 5


@dataclass(frozen=True, slots=True)
class VolumeSpike:
    index: int
    rvol: Decimal
    rvol_class: RvolClass
    quote_volume: Decimal
    direction: str
    conviction: bool
    absorption_candidate: bool


def delta_pct(candle: Candle) -> Decimal | None:
    """Taker buy/sell imbalance as a fraction of volume.

    Binance reports taker *buy* volume, so sells are the remainder and the
    signed delta is `2*buy - total`. Returns None on a zero-volume candle --
    §1.5.4 owns halts, and 0/0 is not a neutral reading.
    """
    if candle.volume <= 0:
        return None

    return (candle.taker_buy_volume * 2 - candle.volume) / candle.volume


def detect_volume_spike(
    candles: Sequence[Candle],
    index: int,
) -> VolumeSpike | None:
    """§6.2. SPIKE or ABNORMAL class **and** the absolute quote floor."""
    rvol = relative_volume(candles, index)
    rvol_class = classify(rvol)

    if rvol is None or rvol_class is None:
        return None

    if rvol_class not in {RvolClass.SPIKE, RvolClass.ABNORMAL}:
        return None

    candle = candles[index]

    if candle.quote_volume < SPIKE_FLOOR_QUOTE:
        return None

    delta = delta_pct(candle)

    body = candle.close - candle.open

    # A spike on a doji is high participation with no progress. §6.2 tags it
    # `absorption_candidate` and scores it neutral -- calling it bullish or
    # bearish from a body of zero would be inventing a direction.
    absorption = body == 0

    if body > 0:
        direction = "UP"
    elif body < 0:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    return VolumeSpike(
        index=index,
        rvol=rvol,
        rvol_class=rvol_class,
        quote_volume=candle.quote_volume,
        direction=direction,
        conviction=delta is not None and abs(delta) >= CONVICTION_DELTA,
        absorption_candidate=absorption,
    )


def detect_expansion(
    candles: Sequence[Candle],
    index: int,
) -> bool:
    """§6.3 expansion: three rising volumes, mean RVOL >= 1.2, real progress.

    All three conditions, because volume rising through a sideways grind is not
    expansion -- it is churn, and scoring it as participation validating a move
    would credit a move that is not happening.
    """
    if index < 2:
        return False

    third, second, first = candles[index], candles[index - 1], candles[index - 2]

    if not (third.volume > second.volume > first.volume):
        return False

    rvols = [relative_volume(candles, i) for i in (index - 2, index - 1, index)]

    if any(value is None for value in rvols):
        return False

    mean_rvol = sum((value for value in rvols if value is not None), Decimal(0)) / 3

    if mean_rvol < EXPANSION_MEAN_RVOL:
        return False

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return False

    progress = abs(third.close - first.open)

    return progress >= EXPANSION_PROGRESS_ATR * atr


def detect_contraction(
    candles: Sequence[Candle],
    index: int,
) -> bool:
    """§6.3 contraction: five-candle mean RVOL and mean range both compressed.

    Both dimensions, explicitly: "volume-only lulls during grinding trends are
    not contraction". A trend that keeps making range on thin volume is still a
    trend, and flagging it as coiling would invert the reading.
    """
    if index < CONTRACTION_WINDOW - 1:
        return False

    window = range(index - CONTRACTION_WINDOW + 1, index + 1)

    rvols = [relative_volume(candles, i) for i in window]

    if any(value is None for value in rvols):
        return False

    mean_rvol = (
        sum((value for value in rvols if value is not None), Decimal(0)) / CONTRACTION_WINDOW
    )

    if mean_rvol > CONTRACTION_MEAN_RVOL:
        return False

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return False

    mean_range = (
        sum((candles[i].high - candles[i].low for i in window), Decimal(0)) / CONTRACTION_WINDOW
    )

    return mean_range <= CONTRACTION_MEAN_RANGE_ATR * atr
