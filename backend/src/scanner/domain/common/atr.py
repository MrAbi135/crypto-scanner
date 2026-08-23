"""Average True Range — the platform's universal volatility unit (SLS §2, §0.4).

Every price-distance threshold in the doctrine is expressed as an ATR multiple
rather than a fixed percentage (§0.4), so this function is the denominator
under the entire detection stack. A wrong ATR does not break one detector; it
quietly shifts every threshold at once.

**Wilder's smoothing, not a simple mean.** §2 specifies
`ATR = (ATR_prev x 13 + TR) / 14`, seeded with the SMA of the first 14 true
ranges. Wilder's is equivalent to an EMA with alpha = 1/14 — an effective lookback
near 27 periods — where a rolling 14-period mean reacts roughly twice as fast.
The codebase previously used the rolling mean, duplicated inline in six replay
services, so every threshold was being evaluated against a materially more
reactive volatility measure than doctrine defines.

That duplication is also why this is a first-class domain object rather than a
seventh private helper: Constitution §29.5 requires *"every analytical concept
is a first-class domain object with a specification, a version, and tests —
never an inline calculation buried in pipeline code."*
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from scanner.domain.common.candle import Candle

ATR_PERIOD = 14

# §0.4: "ε = P.global.tolerance_atr x ATR (default 0.05 x ATR(14)) -- used
# wherever two prices are compared for 'equality'." It lives beside ATR
# because it is meaningless without one, and here rather than in a replay
# service because two of them had already declared their own copy while
# §3.5's BOS had none at all.
TOLERANCE_ATR = Decimal("0.05")

# SLS §0.4: derived quantities are quantised at the recording boundary only.
DERIVED_DP = 4

_DERIVED_EXPONENT = Decimal(1).scaleb(-DERIVED_DP)


def true_range(
    candles: Sequence[Candle],
    index: int,
) -> Decimal:
    """True range at `index` (SLS §2).

    The first candle has no previous close, so its true range degrades to
    high - low. That only matters during seeding, which the §1.9 warm-up gate
    keeps well away from any live decision.
    """

    candle = candles[index]

    if index == 0:
        return candle.high - candle.low

    previous_close = candles[index - 1].close

    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def wilder_atr(
    candles: Sequence[Candle],
    index: int,
) -> Decimal | None:
    """Wilder's smoothed ATR at `index`, or None while still seeding.

    Returns None — not zero — for the first `ATR_PERIOD - 1` candles. Zero
    would be indistinguishable from a genuinely motionless market, and a
    caller dividing by it would produce an answer rather than an error. §1.9's
    warm-up gate means production never reaches this region; the honest return
    is what keeps that true rather than merely likely.
    """

    if index < 0 or index >= len(candles):
        raise IndexError("candle index out of range")

    if index < ATR_PERIOD - 1:
        return None

    seed_window = range(ATR_PERIOD)

    atr = sum(
        (true_range(candles, offset) for offset in seed_window),
        Decimal(0),
    ) / Decimal(ATR_PERIOD)

    for current in range(ATR_PERIOD, index + 1):
        atr = (atr * Decimal(ATR_PERIOD - 1) + true_range(candles, current)) / Decimal(ATR_PERIOD)

    return atr


def quantise_derived(value: Decimal) -> Decimal:
    """Round a derived quantity for recording (SLS §0.4).

    Applies at the recording boundary **only**. Comparisons, thresholds and
    state transitions use unquantised values, so this can never change a
    verdict — it exists so evidence a trader is asked to audit is legible
    rather than twenty-eight digits of arithmetic noise.
    """

    return value.quantize(_DERIVED_EXPONENT, rounding=ROUND_HALF_EVEN)


def wilder_atr_series(
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Wilder ATR at every index, in one pass.

    Identical results to calling `wilder_atr` per index, including the `None`
    seeding region — but that costs O(n) each time, because the recurrence is
    re-seeded from candle zero on every call. Callers that need ATR at many
    points were therefore quadratic, which is why they mostly asked for one
    ATR and reused it across a whole window. Reusing one is not always sound:
    §0.4 makes ATR the denominator under every threshold, so a threshold
    evaluated against the *window's last* ATR changes meaning as the window
    slides, and a detector that re-derives history can then disagree with
    itself between runs.
    """

    if not candles:
        return ()

    out: list[Decimal | None] = [None] * len(candles)

    if len(candles) < ATR_PERIOD:
        return tuple(out)

    atr = sum(
        (true_range(candles, offset) for offset in range(ATR_PERIOD)),
        Decimal(0),
    ) / Decimal(ATR_PERIOD)

    out[ATR_PERIOD - 1] = atr

    for current in range(ATR_PERIOD, len(candles)):
        atr = (atr * Decimal(ATR_PERIOD - 1) + true_range(candles, current)) / Decimal(ATR_PERIOD)
        out[current] = atr

    return tuple(out)
