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
from enum import Enum

from scanner.domain.common import Candle, TradeAggregate
from scanner.domain.common.atr import wilder_atr
from scanner.domain.common.rvol import RvolClass, classify, median, relative_volume

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


@dataclass(frozen=True, slots=True)
class VolumeExpansion:
    """§6.3 expansion, with the direction its own progress test establishes."""

    index: int
    direction: str


def detect_expansion(
    candles: Sequence[Candle],
    index: int,
) -> VolumeExpansion | None:
    """§6.3 expansion: three rising volumes, mean RVOL >= 1.2, real progress.

    All three conditions, because volume rising through a sideways grind is not
    expansion -- it is churn, and scoring it as participation validating a move
    would credit a move that is not happening.

    **Returns the direction, not just a fact.** §6.3's own progress test is
    `|Cl[i] - O[i+2]| >= 0.75 x ATR`, and the sign inside that absolute value
    is the direction of the expansion -- which §6.3 then relies on: "expansion
    in *opposing* direction to an active setup is contrary evidence". This
    function computed the sign and discarded it, so §6.7's "expansion regime
    aligned" had no source, and I declared it unreachable in #71 on the
    strength of that.

    None rather than False, so `if detect_expansion(...)` reads the same at
    every existing call site.
    """
    if index < 2:
        return None

    third, second, first = candles[index], candles[index - 1], candles[index - 2]

    if not (third.volume > second.volume > first.volume):
        return None

    rvols = [relative_volume(candles, i) for i in (index - 2, index - 1, index)]

    if any(value is None for value in rvols):
        return None

    mean_rvol = sum((value for value in rvols if value is not None), Decimal(0)) / 3

    if mean_rvol < EXPANSION_MEAN_RVOL:
        return None

    atr = wilder_atr(candles, index)

    if atr is None or atr <= 0:
        return None

    net = third.close - first.open

    if abs(net) < EXPANSION_PROGRESS_ATR * atr:
        return None

    return VolumeExpansion(index=index, direction="UP" if net > 0 else "DOWN")


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


# §6.4: "trade-count on candle >= 3x its 20-candle median".
TRADE_COUNT_MULTIPLE = Decimal(3)
TRADE_COUNT_WINDOW = 20

# §6.4: "depth >= 50% of its 7-day median".
DEPTH_FLOOR_FRACTION = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AbnormalVolumeCheck:
    """§6.4's cross-validation of one ABNORMAL candle.

    Three outcomes, not two. A test that could not be run is neither a pass
    nor a failure, and collapsing it into either is the mistake this whole
    codebase keeps making: treating it as a pass awards integrity nobody
    measured, treating it as a failure accuses a symbol on no evidence.
    """

    participants_ok: bool | None
    depth_ok: bool | None

    @property
    def suspect(self) -> bool:
        """§6.4: "failing either => tag `suspect_volume`"."""
        return self.participants_ok is False or self.depth_ok is False

    @property
    def validated(self) -> bool:
        """Whether §6.4's "mandatory cross-validation" actually completed.

        §6.4 gates the *positive* contribution on it -- "before it may
        contribute positive score" -- so an incomplete check must not pay,
        even though it also must not tag.
        """
        return self.participants_ok is not None and self.depth_ok is not None


def cross_validate_abnormal_volume(
    candles: Sequence[Candle],
    index: int,
    *,
    depth: Decimal | None = None,
    median_depth_7d: Decimal | None = None,
) -> AbnormalVolumeCheck | None:
    """§6.4, for a candle whose RVOL class is ABNORMAL. None if it is not.

    Test 1 is "many participants, not one wash loop": a candle can only be
    five times its baseline volume on real flow if the count of trades rose
    with it. Test 2 is the book: volume that the depth cannot support is
    tape, not liquidity.

    `depth` and `median_depth_7d` are optional because
    `market.liquidity_history` is empty until the daily universe job has run;
    absent either, the depth half reports None rather than a verdict.
    """
    if index < 0 or index >= len(candles):
        return None

    if classify(relative_volume(candles, index)) is not RvolClass.ABNORMAL:
        return None

    return AbnormalVolumeCheck(
        participants_ok=_participants_ok(candles, index),
        depth_ok=_depth_ok(depth, median_depth_7d),
    )


def _participants_ok(candles: Sequence[Candle], index: int) -> bool | None:
    window = candles[max(0, index - TRADE_COUNT_WINDOW) : index]

    if len(window) < TRADE_COUNT_WINDOW:
        # A short window is not a low median, it is no median. §1.9's warm-up
        # gate keeps production clear of this.
        return None

    baseline = median([Decimal(candle.trade_count) for candle in window])

    if baseline is None or baseline <= 0:
        return None

    return Decimal(candles[index].trade_count) >= TRADE_COUNT_MULTIPLE * baseline


def _depth_ok(depth: Decimal | None, median_depth_7d: Decimal | None) -> bool | None:
    if depth is None or median_depth_7d is None or median_depth_7d <= 0:
        return None

    return depth >= DEPTH_FLOOR_FRACTION * median_depth_7d


# §6.5's four thresholds, none of them tunable.
INSTITUTIONAL_MIN_RVOL = Decimal("1.5")
INSTITUTIONAL_SIZE_SKEW = Decimal(2)
INSTITUTIONAL_MIN_DELTA = Decimal("0.30")
INSTITUTIONAL_MEDIAN_WINDOW = 20


class ParticipationClass(str, Enum):
    """§6.5's reading of who was on the tape."""

    INSTITUTIONAL = "INSTITUTIONAL"
    STEALTH = "STEALTH"
    RETAIL = "RETAIL"


def candle_p90(minutes: Sequence[TradeAggregate]) -> Decimal | None:
    """The candle's 90th-percentile print size, from its minute buckets.

    **An estimate, and unavoidably so.** A percentile does not compose: the
    true p90 over a candle needs every print, and SLS §2 discards them once
    the minute is folded. What survives is one p90 per minute.

    Count-weighted mean of those, rather than the maximum. §6.5 compares this
    against a median of the same statistic over twenty candles, so the number
    only has to mean the same thing on both sides -- and the maximum would let
    one quiet minute holding a single large print speak for the whole candle,
    which is what `max_trade_size` is for.
    """
    if not minutes:
        return None

    prints = sum(item.trade_count for item in minutes)

    if prints <= 0:
        return None

    weighted = sum(
        (item.p90_trade_size * Decimal(item.trade_count) for item in minutes),
        Decimal(0),
    )

    return weighted / Decimal(prints)


def classify_participation(
    *,
    rvol: Decimal | None,
    delta: Decimal | None,
    p90: Decimal | None,
    median_p90: Decimal | None,
    structural: bool,
    suspect: bool,
) -> ParticipationClass | None:
    """§6.5's institutional signature, and the stealth case beside it.

    All four conditions, and the fourth is the one that makes this doctrine
    rather than statistics: "institutional volume *at random locations* is not
    evidence in this doctrine".

    None means the question could not be asked -- no aggTrade coverage, or a
    reading §1.9's warm-up has not produced yet. It is not RETAIL: a symbol
    nobody measured has not been found to be retail flow.

    `suspect` returns RETAIL rather than None. §6.5's validation excludes a
    `suspect_volume` candle outright, and that exclusion *is* an answer -- the
    tape was measured and disqualified, which is different from unmeasured.
    """
    if rvol is None or delta is None:
        return None

    if suspect:
        return ParticipationClass.RETAIL

    if rvol < INSTITUTIONAL_MIN_RVOL:
        return ParticipationClass.RETAIL

    if abs(delta) < INSTITUTIONAL_MIN_DELTA:
        return ParticipationClass.RETAIL

    if not structural:
        return ParticipationClass.RETAIL

    if p90 is None or median_p90 is None or median_p90 <= 0:
        # (1), (3) and (4) hold and the size test cannot be run. That is not
        # the iceberg case -- §6.5's edge case is a candle that *fails* (2),
        # which needs the test to have produced an answer.
        return None

    if p90 >= INSTITUTIONAL_SIZE_SKEW * median_p90:
        return ParticipationClass.INSTITUTIONAL

    # §6.5: "iceberg-style execution (many small prints, strong delta): passes
    # (1)+(3)+(4) but fails (2) => tagged `stealth_flow` -- the size test is
    # sufficient, not necessary, for smart flow".
    return ParticipationClass.STEALTH
