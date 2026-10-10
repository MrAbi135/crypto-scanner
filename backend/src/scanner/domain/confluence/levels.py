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
from scanner.shared import Timeframe

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
RISK_STOP = "risk_stop"

# SLS v1.0.15 §15.2: `P.risk.stop_pct`, the timeframes whose invalidation is a
# fixed risk stop rather than a zone or sweep level, as a percent of the entry's
# proximal edge.
#
# **Deliberately empty.** The doctrine sets M5 at 1.0%, and that value lands in
# the same change as the M5 target ladder and TTL 48, not before. It was
# measured on the 14 published M5 signals that the stop alone is not robust
# (+1.68%, negative once the best two trades are removed) and that the ladder
# alone is worse than the rule it replaces (-4.68%); only the two together
# survive (+6.37%). Switching this on by itself would ship the configuration
# the measurement rejected. Until that change, every lookup here misses and
# every signal keeps the zone rule, so this commit moves no number at all --
# which the untouched golden suite is the evidence for.
RISK_STOP_PCT: dict[Timeframe, Decimal] = {}


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
    def anchor(self) -> Decimal:
        """The entry price R is measured from -- and the ONLY place that decides it.

        §12.4 measures R from the entry **mid**. SLS v1.0.15 measures it from
        the **proximal** edge when the invalidation is a `risk_stop`, because
        that stop is a percentage of the price a fill actually gets, and
        measuring it from the mid would make the published R-multiple flatter
        the achievable one (measured 2026-10-10: about 7-12 published against
        about 4 achievable).

        Keyed on the invalidation's **rule** -- which the sealed payload
        records -- rather than on the timeframe or on `RISK_STOP_PCT`. Keying
        on the parameter would re-read every signal already published under a
        changed setting with today's rule, which is the retroactive relabel
        this codebase does not permit.

        Before this property R was computed in three places, two of which read
        `entry.mid` directly instead of going through `r_unit`: the payload's
        invalidation distance and §12.4's outcome accounting. A rule change
        made here alone would have left both on the old origin, producing
        excursions and distances in a unit nothing else used. All three now
        read this.
        """
        if self.invalidation.rule == RISK_STOP:
            return self.entry.proximal

        return self.entry.mid

    @property
    def r_unit(self) -> Decimal:
        """§12.4: "R = |entry anchor - invalidation|" -- see `anchor`."""

        return abs(self.anchor - self.invalidation.price)

    @property
    def r_multiple(self) -> Decimal | None:
        """Reward in R to the primary target, or None when R is zero.

        A zero R means the invalidation sits on the anchor, which is not a
        tight stop but a broken level pair -- and dividing by it would produce
        an infinite R-multiple that sails through §15.3's floor.
        """
        unit = self.r_unit

        if unit == 0:
            return None

        reach = abs(self.primary_target.near_edge(self.direction) - self.anchor)

        return reach / unit

    @property
    def coherent(self) -> bool:
        """§15.3(1): "entry != invalidation side, target beyond entry in D".

        For a long: invalidation below the anchor, target above it. Both
        strict -- a target level *at* the entry is not somewhere to travel to,
        and an invalidation at the entry is the zero-R case above.

        Measured from `anchor` and not hard-wired to the mid, because the
        difference is not cosmetic for a risk stop: a 1% stop sits ABOVE the
        mid of any zone wider than 2%, so a mid-based check would refuse those
        signals as INCOHERENT_LEVELS although stop, entry and target are in
        perfect order from where the fill happens.
        """
        target = self.primary_target.near_edge(self.direction)

        if self.direction == "UP":
            return self.invalidation.price < self.anchor < target

        return target < self.anchor < self.invalidation.price

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


def risk_stop_for(
    *,
    entry: EntryZone,
    direction: str,
    stop_pct: Decimal,
) -> Invalidation:
    """SLS v1.0.15 §15.2's `risk_stop`: `stop_pct` of the proximal edge, against D.

    Applies whatever the archetype, which is the point of it: on the
    timeframes it covers the stop is no longer the price at which the setup is
    wrong but the price at which the risk budget is spent, so neither the
    zone's distal edge nor a swept extreme has a say.

    When the zone is wider than the stop, the stop lies INSIDE the band and a
    fill deeper than the stop is impossible. §15.2 states that and accepts it
    -- the trade is entered at the proximal edge, and the rest of the band is
    evidence rather than a level the signal relies on -- so it is not an error
    here either.
    """
    if direction not in {"UP", "DOWN"}:
        raise ValueError(f"direction must be UP or DOWN, got {direction!r}")

    if stop_pct <= 0:
        # A zero stop is the zero-R case `r_multiple` already refuses; a
        # negative one would sit on the wrong side of the entry and pass every
        # check that only measures distance.
        raise ValueError(f"stop_pct must be positive, got {stop_pct}")

    distance = entry.proximal * stop_pct / Decimal(100)

    if direction == "UP":
        return Invalidation(price=entry.proximal - distance, rule=RISK_STOP)

    return Invalidation(price=entry.proximal + distance, rule=RISK_STOP)
