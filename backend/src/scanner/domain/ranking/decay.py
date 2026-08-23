"""§9.3's display-rank decay, over §12.5's TTL."""

from __future__ import annotations

from decimal import Decimal

from scanner.shared import Timeframe

# §12.5, in closed candles. `P.lifecycle.ttl` in the parameter table, and §12.5
# adds: "display-rank decay (§9.3) runs across the same window".
#
# W1 is absent, and that is §12.5's own omission rather than a transcription
# slip: the table lists M5, M15, H1, H4 and D1 only, while `Timeframe` carries
# W1 as a scanned timeframe. A W1 setup therefore has no stated TTL, and
# `display_rank` raises rather than inventing one -- a silently assumed 15
# would decay weekly setups at the daily rate and nothing would ever say so.
TTL_CANDLES: dict[Timeframe, int] = {
    Timeframe.M5: 24,
    Timeframe.M15: 24,
    Timeframe.H1: 24,
    Timeframe.H4: 18,
    Timeframe.D1: 15,
}


def ttl_candles(timeframe: Timeframe) -> int:
    """§12.5's TTL for a timeframe, or a loud failure if it states none."""

    try:
        return TTL_CANDLES[timeframe]
    except KeyError:
        raise ValueError(
            f"§12.5 states no TTL for {timeframe.value}; it cannot be assumed"
        ) from None


def display_rank(
    confidence: Decimal,
    *,
    elapsed_candles: int,
    timeframe: Timeframe,
) -> Decimal:
    """§9.3: `display_rank = FinalConfidence x remaining_ttl / ttl`.

    "A published signal's *display rank* (not its recorded confidence) decays
    linearly to zero across its TTL -- stale opportunities sink without their
    historical record changing."

    The parenthesis is the whole point of the function existing: this returns
    a number for a board to sort by and never touches the stored confidence,
    which is a fact about the candle it was computed on.

    At `elapsed_candles = 0` the result is the confidence itself, and at the
    TTL it is exactly zero. Past the TTL it stays zero rather than going
    negative -- §12.5 expires the signal there, and a negative display rank
    would sort a dead setup below a live one with a *worse* score, which is
    not an ordering anyone asked for.
    """
    if elapsed_candles < 0:
        raise ValueError("elapsed_candles cannot be negative")

    ttl = ttl_candles(timeframe)
    remaining = max(0, ttl - elapsed_candles)

    return confidence * Decimal(remaining) / Decimal(ttl)
