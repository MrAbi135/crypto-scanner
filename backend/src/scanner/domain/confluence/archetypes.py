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

from collections.abc import Callable
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


# §8.6's rules as data: one named condition per clause, per archetype.
#
# They were an `if` chain returning a bool, which answered "does this match"
# and nothing else. A classification that returns None is a decision — it is
# what stops a setup publishing, whatever its confidence — and it was the only
# decision in the pipeline that recorded no reason for itself.
#
# On the staging host that cost real time: 63 of 64 setups carried a null
# archetype, one of them at confidence 77 with gates passed, and the database
# could not say which clause had refused them. Naming the clauses makes
# `explain_archetype` and `classify_archetype` read the same table, so the
# answer and the reason can never drift apart.
_RULES: dict[Archetype, tuple[tuple[str, Callable[[ArchetypeEvidence], bool]], ...]] = {
    Archetype.SWEEP_REVERSAL: (
        ("external_sweep", lambda e: e.external_sweep),
        ("mss_confirmed", lambda e: e.mss_confirmed),
        ("mss_origin_zone_retested", lambda e: e.mss_origin_zone_retested),
        ("range_extreme_pd", lambda e: e.range_extreme_pd),
        ("stop_hunt_confirmed", lambda e: e.stop_hunt_confirmed),
    ),
    Archetype.BREAKER_RETEST: (
        ("breaker_formed", lambda e: e.breaker_formed),
        ("breaker_first_retest_respected", lambda e: e.breaker_first_retest_respected),
        # §8.6 allows either the grade or the entry-grade confirmation -- two
        # routes to the same quality claim, not both required.
        (
            "breaker_grade_a_or_entry_grade_confirmation",
            lambda e: e.breaker_grade_a or e.entry_grade_confirmation,
        ),
    ),
    Archetype.CONTINUATION_PULLBACK: (
        ("trend_active", lambda e: e.trend_active),
        ("displaced_bos", lambda e: e.displaced_bos),
        ("retraced_into_zone", lambda e: e.retraced_into_zone),
        ("htf_aligned", lambda e: e.htf_aligned),
        ("retracement_leg", lambda e: e.retracement_leg),
        # "not counter-displacement": a counter-displaced leg is not a pullback
        # at all, it is CHoCH territory (§3.6).
        ("not_counter_displacement", lambda e: not e.counter_displacement),
    ),
    Archetype.FVG_CONTINUATION: (
        ("displacement_fvg", lambda e: e.displacement_fvg),
        ("fvg_first_touch", lambda e: e.fvg_first_touch),
        ("htf_aligned", lambda e: e.htf_aligned),
        ("fvg_age_within_limit", lambda e: 0 <= e.fvg_age_candles <= MAX_FVG_AGE_CANDLES),
    ),
    Archetype.RANGE_LIQUIDITY_PLAY: (
        ("ranging", lambda e: e.ranging),
        ("range_extreme_swept", lambda e: e.range_extreme_swept),
        ("rejection_confirmed", lambda e: e.rejection_confirmed),
        ("range_width_atr", lambda e: e.range_width_atr >= RANGE_MIN_ATR),
    ),
}


@dataclass(frozen=True, slots=True)
class ArchetypeMatch:
    """Which archetype matched, and for the rest, exactly what was missing."""

    archetype: Archetype | None
    # Archetype value -> the named clauses that were false, in rule order.
    # The matched archetype is absent; every other one is present with at
    # least one entry, so an empty dict is impossible unless something matched.
    unmet: dict[str, tuple[str, ...]]

    @property
    def matched(self) -> bool:
        return self.archetype is not None

    @property
    def closest(self) -> str | None:
        """The archetype that failed on the fewest clauses.

        Not a ranking of quality -- §8.6's order is the ranking. This is for a
        human reading a setup that did not classify and wanting to know where
        to look first, which on real data is the question actually asked.
        """
        if self.matched or not self.unmet:
            return None

        return min(self.unmet, key=lambda name: len(self.unmet[name]))


def explain_archetype(evidence: ArchetypeEvidence) -> ArchetypeMatch:
    """§8.6's classification, with its reasoning kept."""

    unmet: dict[str, tuple[str, ...]] = {}

    for archetype in CLASSIFICATION_ORDER:
        failed = tuple(name for name, holds in _RULES[archetype] if not holds(evidence))

        if not failed:
            return ArchetypeMatch(archetype=archetype, unmet=unmet)

        unmet[archetype.value] = failed

    return ArchetypeMatch(archetype=None, unmet=unmet)


def classify_archetype(evidence: ArchetypeEvidence) -> Archetype | None:
    """First match in §8.6 table order, or None if the chain fits nothing.

    None is a real answer: §8.6 says every *publishable* setup must match
    exactly one archetype, so a candidate matching none is simply not a setup —
    not a setup of unknown type.

    Delegates to `explain_archetype` rather than repeating the rules, so the
    verdict and its reasoning cannot disagree.
    """
    return explain_archetype(evidence).archetype


def meets_floor(archetype: Archetype, final_confidence: Decimal) -> bool:
    """§8.6 floors. Below-floor candidates are recorded, never published."""
    return final_confidence >= FLOORS[archetype]


def ranking_priority(archetype: Archetype) -> int:
    """Position in the §9.2 tie-break order. Lower sorts first."""
    return RANKING_PRIORITY.index(archetype)
