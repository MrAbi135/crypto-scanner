"""Impulse and retracement legs (SLS §7.5).

Legs are the skeleton targets, OTE and trend strength all read from, which is
why §7.5 exists at all: without one shared segmentation each of those would
draw its own, and they would disagree.

**Displacement arrives as a set of candle indices, not as objects.** It is
§5.10, and `domain/ict` is a sibling this package may not import. The same
dodge `detect_stop_hunt` uses, and for the same reason — the application layer
knows both engines, so it supplies the crossing fact.

The third outcome matters as much as the two named ones. A counter-direction
leg that displaces, or retraces past 100%, is **neither** impulse nor
retracement: §7.5 escalates it to structure evaluation, because that is what a
CHoCH looks like before it is confirmed (§3.6). Calling it a deep pullback
would file a reversal as a continuation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from itertools import pairwise

from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr
from scanner.domain.structure import SwingPoint

IMPULSE_MIN_ATR = Decimal("1.5")
MICRO_MAX_ATR = Decimal("0.75")
FULL_RETRACE = Decimal(1)


class LegKind(str, Enum):
    IMPULSE = "IMPULSE"
    RETRACEMENT = "RETRACEMENT"

    # Below 0.75 x ATR of net progress. Recorded, but §7.5 excludes it from
    # trend strength and OTE anchoring -- chop should not anchor anything.
    MICRO = "MICRO"

    # Counter-direction with displacement, or retracing past 100%. Not a leg
    # classification at all: it hands the candidate to §3.6.
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class Leg:
    start_index: int
    end_index: int
    start_price: Decimal
    end_price: Decimal
    kind: LegKind
    displaced: bool
    net_progress_atr: Decimal
    retrace_fraction: Decimal | None = None

    @property
    def direction(self) -> str:
        return "UP" if self.end_price > self.start_price else "DOWN"

    @property
    def span(self) -> Decimal:
        return abs(self.end_price - self.start_price)


def segment_legs(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    displacement_indices: frozenset[int],
) -> tuple[Leg, ...]:
    """Split confirmed swings into classified legs, oldest first.

    Only confirmed swings are passed in, which satisfies §7.5's requirement
    that "legs finalize only when their terminal swing confirms" — an unformed
    leg cannot be classified, and guessing at one would repaint.
    """
    if len(swings) < 2:
        return ()

    ordered = sorted(swings, key=lambda s: s.index)

    legs: list[Leg] = []
    previous_impulse: Leg | None = None

    for start, end in pairwise(ordered):
        atr = wilder_atr(candles, end.index)

        if atr is None or atr <= 0:
            continue

        span = abs(end.price - start.price)
        progress_atr = span / atr

        displaced = any(
            index in displacement_indices for index in range(start.index + 1, end.index + 1)
        )

        leg = _classify(
            start=start,
            end=end,
            progress_atr=progress_atr,
            displaced=displaced,
            previous_impulse=previous_impulse,
        )

        legs.append(leg)

        if leg.kind is LegKind.IMPULSE or _supersedes(leg):
            previous_impulse = leg

    return tuple(legs)


def _supersedes(leg: Leg) -> bool:
    """Has this escalating leg taken over as the leg others retrace?

    `previous_impulse` exists for exactly one purpose: to be the leg a
    counter-move is measured against. Re-anchoring it only on `IMPULSE` made
    that a one-way ratchet, because a counter-direction leg can never *be* an
    impulse -- `retrace` is not None for it, and escalation and retracement are
    both checked first. So whichever direction printed the window's first
    impulse held the anchor for the rest of the window, and the other direction
    was locked out of `IMPULSE` permanently.

    Measured on the host, three windows, two symbols, two timeframes -- and
    every one of them lopsided the same way::

        ETHUSDT H1   first impulse DOWN   IMPULSE UP 0  / DOWN 9
        BTCUSDT H1   first impulse UP     IMPULSE UP 12 / DOWN 0
        BTCUSDT H4   first impulse UP     IMPULSE UP 15 / DOWN 0

    with `ESCALATE` filling the locked-out side exactly and nowhere else. That
    complementarity is the signature of a mechanism, not of three markets.
    Downstream, §8.6 A3 asks for a displaced BOS inside the latest impulse leg
    in the candidate's direction, so on any given symbol one of the two
    directions could never classify as A3 at all.

    §7.5 escalates a counter-leg that displaces or retraces past 100% because
    "that is what a CHoCH looks like before it is confirmed" (§3.6) -- which is
    precisely the moment the old anchor stops being what the market is pulling
    back from. Keeping it is the error.

    Strength is required before re-anchoring, though, because escalation is
    deliberately indiscriminate about size: §7.5 escalates a counter-displaced
    leg "regardless of how small it is" so §3.6 can see it. A leg that small is
    a thing to report, not a thing to measure the next pullback against.
    """
    if leg.kind is not LegKind.ESCALATE:
        return False

    # It travelled further than the leg it was retracing. Whatever it is
    # called, it is no longer the smaller half of that pair.
    if leg.retrace_fraction is not None and leg.retrace_fraction > FULL_RETRACE:
        return True

    return leg.displaced and leg.net_progress_atr >= IMPULSE_MIN_ATR


def _classify(
    *,
    start: SwingPoint,
    end: SwingPoint,
    progress_atr: Decimal,
    displaced: bool,
    previous_impulse: Leg | None,
) -> Leg:
    span = abs(end.price - start.price)

    direction = "UP" if end.price > start.price else "DOWN"

    retrace: Decimal | None = None

    if previous_impulse is not None and previous_impulse.span > 0:
        counter = direction != previous_impulse.direction

        if counter:
            retrace = span / previous_impulse.span

    def build(kind: LegKind) -> Leg:
        return Leg(
            start_index=start.index,
            end_index=end.index,
            start_price=start.price,
            end_price=end.price,
            kind=kind,
            displaced=displaced,
            net_progress_atr=progress_atr,
            retrace_fraction=retrace,
        )

    # Escalation is checked before micro. A counter-displaced leg is a
    # structure event regardless of how small it is, and swallowing it as
    # `micro` would hide the very thing §3.6 needs to see.
    if retrace is not None and (displaced or retrace > FULL_RETRACE):
        return build(LegKind.ESCALATE)

    if progress_atr < MICRO_MAX_ATR:
        return build(LegKind.MICRO)

    if retrace is not None:
        return build(LegKind.RETRACEMENT)

    if displaced and progress_atr >= IMPULSE_MIN_ATR:
        return build(LegKind.IMPULSE)

    # Enough progress but no displacement, or displacement without the 1.5x
    # progress: §7.5 requires both for an impulse, and there is no third named
    # category for a with-trend leg that is neither. RETRACEMENT is the
    # residual, never MICRO -- §7.5 defines micro as "below 0.75 x ATR net
    # progress", and everything reaching this line already cleared that bar.
    # The first draft sent the 0.75-1.5 band to MICRO, which silently removed
    # every moderate with-trend leg from trend strength and OTE anchoring.
    return build(LegKind.RETRACEMENT)


def anchoring_legs(legs: Sequence[Leg]) -> tuple[Leg, ...]:
    """Legs that may anchor trend strength and OTE (§7.5 excludes micro)."""
    return tuple(leg for leg in legs if leg.kind is not LegKind.MICRO)
