"""§12's signal state machine, and what one closed candle does to a signal.

§12: *"One immutable state machine governs every signal."* The diagram and the
prose disagree about nothing, which is rare enough to say — the edges below
are transcribed from the diagram and each guard from §12.3 and §12.4.

Two asymmetries are deliberate and both are load-bearing:

* **Invalidation needs a close; the target needs only a touch.** §12.3 says a
  wick through the invalidation "records `stress_test: true` but does not fail
  the signal", while for targets "a wick into the pool is the pool being
  consumed". A scanner that failed on wicks would be stopped out by every
  liquidity grab it exists to detect.
* **`INVALIDATED_EARLY` is reachable only from `PUBLISHED`.** §12.3 says
  "pre-touch only": once price is in the zone the premise has already been
  acted on, and a late premise break is what `FAILED` is for.

**One candle cannot establish "before".** §12.4 awards SUCCESS when "target
touched before invalidation close", and a single candle whose range covers the
target and whose close is beyond the invalidation satisfies both halves with
no way to order them. This resolves to FAILED. §15.4 puts the platform's
record above its numbers, and awarding the favourable reading of an
unknowable order is exactly the flattery that ruins a track record. Recorded
with a reason that says the order was indeterminate, so the count is auditable
rather than merely conservative.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.confluence import SignalLevels


class SignalState(str, Enum):
    """§12's nine states."""

    DETECTED = "DETECTED"
    PUBLISHED = "PUBLISHED"
    SUPPRESSED = "SUPPRESSED"
    ACTIVE = "ACTIVE"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    EXPIRED_UNTOUCHED = "EXPIRED_UNTOUCHED"
    EXPIRED_ACTIVE = "EXPIRED_ACTIVE"
    INVALIDATED_EARLY = "INVALIDATED_EARLY"


TERMINAL_STATES = frozenset(
    {
        SignalState.SUPPRESSED,
        SignalState.SUCCESS,
        SignalState.FAILED,
        SignalState.EXPIRED_UNTOUCHED,
        SignalState.EXPIRED_ACTIVE,
        SignalState.INVALIDATED_EARLY,
    }
)


# Transcribed edge for edge from §12's diagram. A dict rather than a chain of
# ifs so an edge nobody drew cannot be taken by accident.
_ALLOWED: dict[SignalState, frozenset[SignalState]] = {
    SignalState.DETECTED: frozenset({SignalState.PUBLISHED, SignalState.SUPPRESSED}),
    SignalState.PUBLISHED: frozenset(
        {
            SignalState.ACTIVE,
            SignalState.EXPIRED_UNTOUCHED,
            SignalState.INVALIDATED_EARLY,
        }
    ),
    SignalState.ACTIVE: frozenset(
        {
            SignalState.SUCCESS,
            SignalState.FAILED,
            SignalState.EXPIRED_ACTIVE,
        }
    ),
}


def may_transition(source: SignalState, target: SignalState) -> bool:
    """Whether §12's diagram draws this edge."""

    return target in _ALLOWED.get(source, frozenset())


@dataclass(frozen=True, slots=True)
class Observation:
    """What one closed candle did to a signal.

    `to_state` is None when the candle changed nothing, which is the common
    case and is not a failure. `stress_test` can be true alongside either --
    §12.3 records a wick through the invalidation as a fact about the candle
    whether or not the signal also moved.
    """

    to_state: SignalState | None = None
    stress_test: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Candle:
    """The four prices §12.3 reads. Deliberately not the full candle."""

    high: Decimal
    low: Decimal
    close: Decimal


def observe(
    state: SignalState,
    candle: Candle,
    *,
    levels: SignalLevels,
    elapsed_candles: int,
    ttl_candles: int,
    premise_broken: bool = False,
) -> Observation:
    """§12.3's per-candle monitoring, for one signal on its own timeframe.

    Order of evaluation is the doctrine's, not convenience: invalidation and
    target are read before the TTL, because a signal that resolved on the same
    candle its TTL lapsed resolved -- §12.4 counts it, and "expired" would
    hide a real outcome behind a clock.
    """
    if state in TERMINAL_STATES:
        return Observation()

    stress = _wicked_through(candle, levels)

    if state is SignalState.PUBLISHED:
        if premise_broken:
            return Observation(
                SignalState.INVALIDATED_EARLY,
                stress,
                "premise destroyed before the entry was touched",
            )

        if _touched_entry(candle, levels):
            return Observation(SignalState.ACTIVE, stress, "entry zone touched")

        if elapsed_candles >= ttl_candles:
            return Observation(
                SignalState.EXPIRED_UNTOUCHED,
                stress,
                "TTL lapsed with the entry never touched",
            )

        return Observation(None, stress)

    # ACTIVE.
    hit_target = _touched_target(candle, levels)
    closed_through = _closed_beyond_invalidation(candle, levels)

    if hit_target and closed_through:
        return Observation(
            SignalState.FAILED,
            stress,
            "target and invalidation both met on one candle; order indeterminate",
        )

    if hit_target:
        return Observation(SignalState.SUCCESS, stress, "target zone touched")

    if closed_through:
        return Observation(SignalState.FAILED, stress, "closed beyond the invalidation")

    if elapsed_candles >= ttl_candles:
        return Observation(
            SignalState.EXPIRED_ACTIVE,
            stress,
            "TTL lapsed while in the position range",
        )

    return Observation(None, stress)


def _touched_entry(candle: Candle, levels: SignalLevels) -> bool:
    """§12.3's "entry-zone touch check".

    A touch is the candle's range reaching the band, not its close being
    inside it -- price that traded into the zone and left has still filled an
    order resting there.
    """
    low, high = sorted((levels.entry.proximal, levels.entry.distal))

    return candle.low <= high and candle.high >= low


def _touched_target(candle: Candle, levels: SignalLevels) -> bool:
    """§12.3: "touch of target zone suffices"."""

    target = levels.primary_target

    if levels.direction == "UP":
        return candle.high >= target.low

    return candle.low <= target.high


def _closed_beyond_invalidation(candle: Candle, levels: SignalLevels) -> bool:
    """§12.3's invalidation check: a **close** beyond the level."""

    if levels.direction == "UP":
        return candle.close < levels.invalidation.price

    return candle.close > levels.invalidation.price


def _wicked_through(candle: Candle, levels: SignalLevels) -> bool:
    """§12.3's `stress_test`: the wick went through, the close did not.

    Recorded as a fact about the candle rather than a state change. §5.9's
    zone grammar treats a wick the same way, and §12.3 says so explicitly --
    "consistent with zone grammar".
    """
    price = levels.invalidation.price

    if levels.direction == "UP":
        return candle.low < price <= candle.close

    return candle.high > price >= candle.close
