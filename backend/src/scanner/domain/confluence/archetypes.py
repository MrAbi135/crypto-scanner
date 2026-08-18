"""Setup archetypes (SLS §8.6) and their publication floors.

**Two orderings live here, and they are not the same.** Conflating them is an
invisible mistake, so they are separate constants with separate tests:

* **Classification** (§8.6) is rule-ordered, first match wins, in table order:
  A1, A2, A3, A4, A5.
* **Ranking tie-break** (§9.2) is A1 > A2 > A5 > A3 > A4, because reversal-class
  setups are rarer and time-critical.

Every publishable setup must match exactly one archetype, and each carries its
own confidence floor. §8.6 calls the floor "the quality-over-quantity
mechanism": a below-floor candidate is recorded for calibration and never
published.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Archetype(str, Enum):
    SWEEP_REVERSAL = "A1"
    BREAKER_RETEST = "A2"
    CONTINUATION_PULLBACK = "A3"
    FVG_CONTINUATION = "A4"
    RANGE_LIQUIDITY_PLAY = "A5"


# §8.6 confidence floors. Reversal classes sit higher because they trade
# against the prevailing state and need more evidence to earn publication.
FLOORS: dict[Archetype, Decimal] = {
    Archetype.SWEEP_REVERSAL: Decimal(75),
    Archetype.BREAKER_RETEST: Decimal(72),
    Archetype.CONTINUATION_PULLBACK: Decimal(70),
    Archetype.FVG_CONTINUATION: Decimal(70),
    Archetype.RANGE_LIQUIDITY_PLAY: Decimal(74),
}

# §8.6: rule-ordered, first match wins.
CLASSIFICATION_ORDER: tuple[Archetype, ...] = (
    Archetype.SWEEP_REVERSAL,
    Archetype.BREAKER_RETEST,
    Archetype.CONTINUATION_PULLBACK,
    Archetype.FVG_CONTINUATION,
    Archetype.RANGE_LIQUIDITY_PLAY,
)

# §9.2 tie-break priority -- deliberately NOT the classification order.
RANKING_PRIORITY: tuple[Archetype, ...] = (
    Archetype.SWEEP_REVERSAL,
    Archetype.BREAKER_RETEST,
    Archetype.RANGE_LIQUIDITY_PLAY,
    Archetype.CONTINUATION_PULLBACK,
    Archetype.FVG_CONTINUATION,
)

MAX_FVG_AGE_CANDLES = 30
RANGE_MIN_ATR = Decimal(2)


@dataclass(frozen=True, slots=True)
class ArchetypeEvidence:
    """The chain facts each archetype's rule reads.

    Supplied by the application layer, which is the only place allowed to read
    across structure, liquidity, zone, volume and momentum at once.
    """

    # A1 -- external sweep -> MSS -> retest of the MSS-origin zone.
    external_sweep: bool = False
    mss_confirmed: bool = False
    mss_origin_zone_retested: bool = False
    stop_hunt_confirmed: bool = False
    range_extreme_pd: bool = False

    # A2 -- breaker formed, first retest respected.
    breaker_formed: bool = False
    breaker_first_retest_respected: bool = False
    breaker_grade_a: bool = False
    entry_grade_confirmation: bool = False

    # A3 -- trend + displaced BOS -> retrace into OTE/OB/FVG.
    trend_active: bool = False
    displaced_bos: bool = False
    retraced_into_zone: bool = False
    htf_aligned: bool = False
    retracement_leg: bool = False
    counter_displacement: bool = False

    # A4 -- displacement FVG, first touch with trend.
    displacement_fvg: bool = False
    fvg_first_touch: bool = False
    fvg_age_candles: int = 0

    # A5 -- ranging, sweep of a range extreme, rejection.
    ranging: bool = False
    range_extreme_swept: bool = False
    rejection_confirmed: bool = False
    range_width_atr: Decimal = Decimal(0)


def _matches(archetype: Archetype, e: ArchetypeEvidence) -> bool:
    if archetype is Archetype.SWEEP_REVERSAL:
        return (
            e.external_sweep
            and e.mss_confirmed
            and e.mss_origin_zone_retested
            and e.range_extreme_pd
            and e.stop_hunt_confirmed
        )

    if archetype is Archetype.BREAKER_RETEST:
        return (
            e.breaker_formed
            and e.breaker_first_retest_respected
            # §8.6 allows either the grade or the entry-grade confirmation --
            # two routes to the same quality claim, not both required.
            and (e.breaker_grade_a or e.entry_grade_confirmation)
        )

    if archetype is Archetype.CONTINUATION_PULLBACK:
        return (
            e.trend_active
            and e.displaced_bos
            and e.retraced_into_zone
            and e.htf_aligned
            and e.retracement_leg
            # "not counter-displacement": a counter-displaced leg is not a
            # pullback at all, it is CHoCH territory (§3.6).
            and not e.counter_displacement
        )

    if archetype is Archetype.FVG_CONTINUATION:
        return (
            e.displacement_fvg
            and e.fvg_first_touch
            and e.htf_aligned
            and 0 <= e.fvg_age_candles <= MAX_FVG_AGE_CANDLES
        )

    return (
        e.ranging
        and e.range_extreme_swept
        and e.rejection_confirmed
        and e.range_width_atr >= RANGE_MIN_ATR
    )


def classify_archetype(evidence: ArchetypeEvidence) -> Archetype | None:
    """First match in §8.6 table order, or None if the chain fits nothing.

    None is a real answer: §8.6 says every *publishable* setup must match
    exactly one archetype, so a candidate matching none is simply not a setup —
    not a setup of unknown type.
    """
    for archetype in CLASSIFICATION_ORDER:
        if _matches(archetype, evidence):
            return archetype

    return None


def meets_floor(archetype: Archetype, final_confidence: Decimal) -> bool:
    """§8.6 floors. Below-floor candidates are recorded, never published."""
    return final_confidence >= FLOORS[archetype]


def ranking_priority(archetype: Archetype) -> int:
    """Position in the §9.2 tie-break order. Lower sorts first."""
    return RANKING_PRIORITY.index(archetype)
