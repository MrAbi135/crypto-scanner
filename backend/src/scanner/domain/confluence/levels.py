"""§15.2's entry, invalidation and target levels (SLS §12, §15).

§12.1 instantiates a signal "with the complete §15.2 payload", and three of
that payload's rows are prices rather than evidence: the entry zone, the
invalidation level, and the target zones. Nothing computed them — the
confluence engine produced a confidence and a zone id and stopped there, so
§15.3's publication checks had nothing to check and §12.3's monitoring had no
levels to watch.

Two of the rules here are not stated as tables anywhere and are read out of
what the archetypes *are*, so both are spelled out at their call site:

* **Invalidation.** §15.2 says "zone distal edge / swept extreme per
  archetype" and leaves the mapping to the reader. A1 and A5 are sweep
  theses — "external sweep → MSS → retest" and "sweep of range extreme →
  rejection" — and what kills them is price returning through the swept
  extreme. A2, A3 and A4 are zone theses, and what kills those is the zone
  failing, which §5's grammar already calls a close beyond the distal edge.

* **Targets.** §15.2 says "nearest opposing external liquidity pool band",
  but §8.6 gives A5 its own: "Target = opposing range extreme". The
  archetype's own row wins over the general rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.confluence.archetypes import Archetype

# §15.3(3): "R-multiple to primary target >= P.quality.min_rr = 1.5 (a
# structurally valid setup with no room to travel is not an opportunity)".
MIN_RR = Decimal("1.5")

# The archetypes whose thesis is a sweep rather than a zone -- see the module
# docstring. Kept as a set rather than an if-chain so adding an archetype
# forces a decision here instead of silently defaulting to the zone rule.
_SWEPT_EXTREME_ARCHETYPES = frozenset(
    {
        Archetype.SWEEP_REVERSAL,
        Archetype.RANGE_LIQUIDITY_PLAY,
    }
)

ZONE_DISTAL_EDGE = "zone_distal_edge"
SWEPT_EXTREME = "swept_extreme"


@dataclass(frozen=True, slots=True)
class EntryZone:
    """§15.2: "zone band [proximal, distal] + zone object id + refined sub-zone".

    Proximal is the edge price meets first and distal the far one, so which
    of a band's two prices is which depends on the direction. A long entering
    a demand zone from above meets its high first; a short meets a supply
    zone's low first. Storing them as `low`/`high` and letting each reader
    work it out is how one of them eventually gets it backwards.
    """

    zone_id: str
    proximal: Decimal
    distal: Decimal
    refined_proximal: Decimal | None = None
    refined_distal: Decimal | None = None

    @property
    def mid(self) -> Decimal:
        """§12.4's entry mid, which R is measured from."""

        return (self.proximal + self.distal) / Decimal(2)


@dataclass(frozen=True, slots=True)
class Invalidation:
    """§15.2: "exact price level + rule that set it"."""

    price: Decimal
    rule: str


@dataclass(frozen=True, slots=True)
class TargetBand:
    """§15.2: a target with "pool ids and strengths"."""

    low: Decimal
    high: Decimal
    pool_id: str | None = None
    strength: Decimal | None = None

    def near_edge(self, direction: str) -> Decimal:
        """The edge that counts as reached.

        §12.3: "target check -- **touch** of target zone suffices (targets are
        liquidity pools; a wick into the pool is the pool being consumed)".
        Touching the near edge is touching the pool, so distance to target is
        measured to it and not to the middle.
        """
        return self.low if direction == "UP" else self.high


@dataclass(frozen=True, slots=True)
class SignalLevels:
    """The three priced rows of §15.2, and the R they imply."""

    direction: str
    entry: EntryZone
    invalidation: Invalidation
    primary_target: TargetBand
    secondary_target: TargetBand | None = None

    @property
    def r_unit(self) -> Decimal:
        """§12.4: "R = |entry mid - invalidation|"."""

        return abs(self.entry.mid - self.invalidation.price)

    @property
    def r_multiple(self) -> Decimal | None:
        """Reward in R to the primary target, or None when R is zero.

        A zero R means the invalidation sits on the entry mid, which is not a
        tight stop but a broken level pair -- and dividing by it would produce
        an infinite R-multiple that sails through §15.3's floor.
        """
        unit = self.r_unit

        if unit == 0:
            return None

        reach = abs(self.primary_target.near_edge(self.direction) - self.entry.mid)

        return reach / unit

    @property
    def coherent(self) -> bool:
        """§15.3(1): "entry != invalidation side, target beyond entry in D".

        For a long: invalidation below the entry mid, target above it. Both
        strict -- a target level *at* the entry is not somewhere to travel to,
        and an invalidation at the entry is the zero-R case above.
        """
        target = self.primary_target.near_edge(self.direction)

        if self.direction == "UP":
            return self.invalidation.price < self.entry.mid < target

        return target < self.entry.mid < self.invalidation.price

    @property
    def meets_rr(self) -> bool:
        """§15.3(3), and False when R cannot be computed at all."""

        multiple = self.r_multiple

        return multiple is not None and multiple >= MIN_RR


def entry_zone(
    *,
    zone_id: str,
    direction: str,
    band_low: Decimal,
    band_high: Decimal,
    refined_low: Decimal | None = None,
    refined_high: Decimal | None = None,
) -> EntryZone:
    """Orient a zone's band into proximal and distal for `direction`."""

    if band_high < band_low:
        raise ValueError("band_high must be >= band_low")

    if direction == "UP":
        return EntryZone(
            zone_id=zone_id,
            proximal=band_high,
            distal=band_low,
            refined_proximal=refined_high,
            refined_distal=refined_low,
        )

    return EntryZone(
        zone_id=zone_id,
        proximal=band_low,
        distal=band_high,
        refined_proximal=refined_low,
        refined_distal=refined_high,
    )


def invalidation_for(
    archetype: Archetype,
    *,
    entry: EntryZone,
    swept_extreme: Decimal | None,
) -> Invalidation | None:
    """§15.2's "zone distal edge / swept extreme per archetype".

    Returns None when the archetype wants a swept extreme and none is
    recorded. That is a payload the signal cannot be published with (§15.3
    requires every field non-null), and inventing the zone edge instead would
    hand an A1 the wrong stop entirely -- the MSS-origin zone can sit far
    inside the swing that was swept.
    """
    if archetype in _SWEPT_EXTREME_ARCHETYPES:
        if swept_extreme is None:
            return None

        return Invalidation(price=swept_extreme, rule=SWEPT_EXTREME)

    return Invalidation(price=entry.distal, rule=ZONE_DISTAL_EDGE)
