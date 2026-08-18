"""Trend strength (SLS §7.4) — a 0-100 composite, purely derived.

Three parts, and §7.4 sets each ceiling:

* **structural quality (0-40)** — consecutive unbroken HH/HL or LL/LH pairs
* **momentum alignment (0-30)** — score direction agrees with trend state
* **pullback shallowness (0-30)** — mean retracement depth of the last three
  legs against the OTE band

§7.4 says "purely derived — no new detection surface", so nothing here reads
candles. It reads what the other engines already decided, which is also why it
returns its three parts alongside the total: a composite nobody can decompose
cannot be argued with.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.momentum.legs import Leg, LegKind
from scanner.domain.momentum.score import MomentumDirection

STRUCTURAL_MAX = Decimal(40)
ALIGNMENT_MAX = Decimal(30)
PULLBACK_MAX = Decimal(30)

# Full structural marks at four consecutive unbroken pairs. Beyond that the
# evidence stops improving -- a trend eight pairs old is not twice as strong as
# one four pairs old, it is just older.
STRUCTURAL_FULL_PAIRS = 4

# The OTE band is 0.62-0.79 (§5.8). A pullback shallower than its low edge is
# the strongest form; deeper than the high edge earns nothing.
OTE_SHALLOW = Decimal("0.62")
OTE_DEEP = Decimal("0.79")

PULLBACK_LEGS = 3


@dataclass(frozen=True, slots=True)
class TrendStrength:
    total: Decimal
    structural: Decimal
    alignment: Decimal
    pullback: Decimal
    pairs_counted: int
    legs_counted: int


def trend_strength(
    *,
    unbroken_pairs: int,
    trend_direction: str,
    momentum_direction: MomentumDirection,
    legs: Sequence[Leg],
) -> TrendStrength:
    """§7.4. `trend_direction` is the §3.4 state: UP, DOWN or RANGING."""
    structural = _structural(unbroken_pairs)
    alignment = _alignment(trend_direction, momentum_direction)
    pullback, counted = _pullback(legs)

    return TrendStrength(
        total=structural + alignment + pullback,
        structural=structural,
        alignment=alignment,
        pullback=pullback,
        pairs_counted=max(0, unbroken_pairs),
        legs_counted=counted,
    )


def _structural(pairs: int) -> Decimal:
    if pairs <= 0:
        return Decimal(0)

    if pairs >= STRUCTURAL_FULL_PAIRS:
        return STRUCTURAL_MAX

    return STRUCTURAL_MAX * Decimal(pairs) / Decimal(STRUCTURAL_FULL_PAIRS)


def _alignment(trend_direction: str, momentum_direction: MomentumDirection) -> Decimal:
    # A RANGING trend has no direction to agree with, and NEUTRAL momentum
    # asserts none -- neither is disagreement, but neither is alignment either.
    if trend_direction not in {"UP", "DOWN"}:
        return Decimal(0)

    if momentum_direction is MomentumDirection.NEUTRAL:
        return Decimal(0)

    return ALIGNMENT_MAX if momentum_direction.value == trend_direction else Decimal(0)


def _pullback(legs: Sequence[Leg]) -> tuple[Decimal, int]:
    """Shallower retracements score higher, measured against the OTE band.

    Micro legs are excluded by §7.5 and escalated legs are not retracements at
    all, so both are filtered before the mean is taken -- averaging them in
    would let chop flatter a trend.
    """
    retracements = [
        leg.retrace_fraction
        for leg in legs
        if leg.kind is LegKind.RETRACEMENT and leg.retrace_fraction is not None
    ]

    recent = retracements[-PULLBACK_LEGS:]

    if not recent:
        return Decimal(0), 0

    mean = sum(recent, Decimal(0)) / len(recent)

    if mean <= OTE_SHALLOW:
        return PULLBACK_MAX, len(recent)

    if mean >= OTE_DEEP:
        return Decimal(0), len(recent)

    # Linear across the band: at its shallow edge full marks, at its deep edge
    # none, so a pullback sitting mid-OTE scores mid.
    span = OTE_DEEP - OTE_SHALLOW

    return PULLBACK_MAX * (OTE_DEEP - mean) / span, len(recent)
