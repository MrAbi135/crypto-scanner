"""§4.2's `touches`: approaches that reversed at a pool without breaching it.

**This was a hardcoded zero on every pool ever built.** Both call sites in
`liquidity_replay` passed `touches=0`, so `touches_component` was exactly `0` on
all 1,411 pools on the host and the strength formula could not exceed 75 of its
100 points. §4.2's whole purpose is to rank pools against each other, and a
quarter of the score that never varies cannot contribute to a ranking.

The same defect was found and fixed for `cluster_factor` -- see
`pool_from_cluster`'s docstring -- which is the reason to state plainly what
this one is: a constant wearing a formula.

**What §4.2 asks for.** *"`touches` = number of separate approaches that
reversed within `ε` without breaching"*. Three words carry the definition:

*approach* -- the candle reached the pool's zone. For a BSL pool that means the
high came within `ε` of the level; the pool's own band widens the zone, because
a cluster pool's stops sit across the band rather than on one price (§4.2:
*"the cluster band ... is retained for sweep tolerance"*).

*without breaching* -- the candle did **not** go beyond the far edge of the zone
by more than `ε`. A candle that did is a sweep or a break, and §4.6 and §4.2's
state machine already own it. Counting it here would pay a pool for the event
that ends it.

*separate* -- consecutive candles in the zone are one approach, not five. Price
has to leave the zone before the next approach can begin. Without this the
component would measure how long price loitered rather than how many times it
was rejected, and a slow drift along a level would score the maximum.

An approach still in progress at the end of the series is not counted. It has
not reversed yet, and it may yet breach; §4.2 asks for approaches that
*reversed*, in the past tense.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.liquidity.model import LiquiditySide

# §4.2 scores `min(touches, 3)`, so nothing above three changes an answer.
# Stopping there is not an optimisation -- it bounds what a long window can do
# to the count, which matters because the candles available to count over are
# whatever the sliding window holds.
MAX_COUNTED_TOUCHES = 3


def count_pool_touches(
    candles: Sequence[Candle],
    *,
    side: LiquiditySide,
    band_low: Decimal,
    band_high: Decimal,
    epsilon: Decimal,
) -> int:
    """Count §4.2's separate reversing approaches over `candles`.

    `candles` are the closed candles **after** the pool's confirmation, oldest
    first. A pool is not touched by the candle that created it.

    `epsilon` is the same `ε` the rest of §4 uses for level equality, supplied
    by the caller because it is ATR-derived and this stays pure.
    """
    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")

    if band_high < band_low:
        raise ValueError("band_high cannot be below band_low")

    touches = 0
    inside = False
    breached = False

    for candle in candles:
        in_zone = _reaches(candle, side, band_low, band_high, epsilon)

        if in_zone:
            if not inside:
                inside = True
                breached = False

            # Once an approach breaches it stays disqualified for the whole
            # run: a candle that pokes through and a later one in the same run
            # that does not are one approach, and it went through.
            breached = breached or _breaches(candle, side, band_low, band_high, epsilon)

            continue

        if inside:
            # Price has left the zone. The approach is over, and it counts if
            # it never went beyond the level.
            if not breached:
                touches += 1

                if touches >= MAX_COUNTED_TOUCHES:
                    return touches

            inside = False
            breached = False

    # A run still open at the end is deliberately not counted -- see the module
    # docstring. It has not reversed, and the next candle may breach it.
    return touches


def _reaches(
    candle: Candle,
    side: LiquiditySide,
    band_low: Decimal,
    band_high: Decimal,
    epsilon: Decimal,
) -> bool:
    """Did this candle get into the pool's zone?

    Measured against the near edge, which is the first price an approach meets:
    the band's low for a pool overhead, its high for one below.
    """
    if side is LiquiditySide.BSL:
        return candle.high >= band_low - epsilon

    return candle.low <= band_high + epsilon


def _breaches(
    candle: Candle,
    side: LiquiditySide,
    band_low: Decimal,
    band_high: Decimal,
    epsilon: Decimal,
) -> bool:
    """Did it go beyond the far edge, by more than `ε`?

    The far edge and not the near one. A candle that enters the band and turns
    inside it is the textbook rejection this component exists to count; only
    passing all the way through is the sweep §4.6 owns.
    """
    if side is LiquiditySide.BSL:
        return candle.high > band_high + epsilon

    return candle.low < band_low - epsilon
