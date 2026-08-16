"""The warm-up gate (SLS §1.9).

Doctrine refuses to analyse a series that is too short to analyse. §1.9's
rationale is explicit about why: *"swing structure, ATR baselines, and RVOL
medians are statistically undefined on short histories; listing-day price
action is dominated by allocation flows and is untradeable by structural
doctrine."*

This is a first-class domain object rather than an inline `len(candles) > n`
in each service, because Constitution §29.5 requires exactly that: *"Every
analytical concept is a first-class domain object with a specification, a
version, and tests — never an inline calculation buried in pipeline code."*
ATR is currently the counter-example, duplicated inline six times; this module
exists so warm-up does not become the seventh.

**Only the candle-count half of §1.9 is enforced here.** The rule also requires
≥ 14 calendar days since listing, which cannot be evaluated today:

* `market.symbols` is empty in every environment — `symbol_sync` has never run,
  so there is no registry to ask.
* `Symbol` carries `first_seen_at`, not a listing date. They are not the same
  fact, and substituting one for the other would put every long-established
  symbol into warm-up on the first day of any fresh deployment — a false
  negative that hides real signals rather than filtering noise.

Implementing that half needs a real `listed_at` sourced from the venue. Until
then a symbol can pass this gate on candle count while being genuinely newly
listed, so the gate is necessary but not yet sufficient.
"""

from __future__ import annotations

from enum import Enum

# SLS §1.9, per timeframe.
DETECTION_MIN_CANDLES = 300
VOLUME_MOMENTUM_MIN_CANDLES = 100

# SLS §1.9, not enforced — see the module docstring.
LISTING_MIN_DAYS = 14


class WarmupCapability(str, Enum):
    """The capability classes §1.9 gates, each with its own threshold."""

    DETECTION = "DETECTION"
    """Structure, liquidity and ICT detection: >= 300 closed candles."""

    VOLUME = "VOLUME"
    """Volume / RVOL analytics: >= 100 closed candles."""

    MOMENTUM = "MOMENTUM"
    """Momentum analytics: >= 100 closed candles."""


def minimum_candles(capability: WarmupCapability) -> int:
    """Return the §1.9 closed-candle floor for one capability."""

    if capability is WarmupCapability.DETECTION:
        return DETECTION_MIN_CANDLES

    return VOLUME_MOMENTUM_MIN_CANDLES


def is_warm(
    capability: WarmupCapability,
    *,
    closed_candles: int,
) -> bool:
    """Whether `closed_candles` clears the §1.9 floor for `capability`.

    The comparison is inclusive: §1.9 states the requirement as "≥ 300 closed
    candles", so a series of exactly 300 is warm.
    """

    if closed_candles < 0:
        raise ValueError("closed_candles must be non-negative")

    return closed_candles >= minimum_candles(capability)


def detection_is_warm(closed_candles: int) -> bool:
    """Shorthand for the gate every detection engine shares."""

    return is_warm(
        WarmupCapability.DETECTION,
        closed_candles=closed_candles,
    )
