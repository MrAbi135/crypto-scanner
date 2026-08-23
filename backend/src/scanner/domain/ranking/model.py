"""What §9.2 needs to know about a setup in order to place it (SLS §9)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence import Archetype
from scanner.shared import Timeframe

# §9.2's fourth tie-break is "higher liquidity tier". T1 is the most liquid, so
# the enum's own order is the ranking order and lower sorts first. INELIGIBLE
# is here for completeness rather than because it can appear: G1 refuses a
# symbol whose tier does not permit the timeframe, so a setup never reaches
# ranking on one. Ordering it last is the answer that stays right if that ever
# changes.
_TIER_ORDER: dict[UniverseTier, int] = {
    UniverseTier.T1: 0,
    UniverseTier.T2: 1,
    UniverseTier.T3: 2,
    UniverseTier.INELIGIBLE: 3,
}


def tier_priority(tier: UniverseTier) -> int:
    """Position in §9.2's liquidity-tier tie-break. Lower sorts first."""

    return _TIER_ORDER[tier]


@dataclass(frozen=True, slots=True)
class RankableSetup:
    """One published setup, reduced to the five things §9.2 orders on.

    Deliberately not the whole `SetupCandidate`. §9.2 is a total order over
    five stated keys, and a ranking function that could see the factor scores
    could quietly start using them -- the doctrine's ordering would then live
    in two places, one of them undocumented.
    """

    symbol: str
    timeframe: Timeframe
    confidence: Decimal
    archetype: Archetype
    tier: UniverseTier

    # Not one of §9.2's five keys. It is here because a signal's identity
    # includes which way it points, and because §9.2's chain ends at the
    # symbol -- two opposite-direction signals on one symbol with equal
    # confidence, archetype, TF and tier would tie on every stated key, and
    # `sorted` would then fall back to arrival order. S8's DoD forbids exactly
    # that ("shuffled symbol order -> identical ranking"), so `order` breaks
    # the remaining tie on this. It cannot change any ordering §9.2 states; it
    # only decides one §9.2 leaves open.
    direction: str = "UP"
