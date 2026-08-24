"""§15.2's priced rows, and §15.3's checks on them."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    MIN_RR,
    SWEPT_EXTREME,
    ZONE_DISTAL_EDGE,
    Archetype,
    SignalLevels,
    TargetBand,
    entry_zone,
    invalidation_for,
)


def zone(direction: str = "UP"):
    return entry_zone(
        zone_id="z1",
        direction=direction,
        band_low=Decimal(100),
        band_high=Decimal(104),
    )


def levels(
    *,
    direction: str = "UP",
    invalidation_price: str = "98",
    target_low: str = "112",
    target_high: str = "114",
) -> SignalLevels:
    from scanner.domain.confluence.levels import Invalidation

    return SignalLevels(
        direction=direction,
        entry=zone(direction),
        invalidation=Invalidation(Decimal(invalidation_price), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(
            low=Decimal(target_low),
            high=Decimal(target_high),
            pool_id="p1",
            strength=Decimal(60),
        ),
    )


def test_proximal_is_the_edge_price_meets_first_and_it_flips_with_direction() -> None:
    """A long meets a demand zone's high first; a short meets a supply low first.

    Storing the band as low/high and letting each reader decide is how one of
    them eventually gets it backwards -- and backwards here means the stop and
    the entry swap ends.
    """
    long_entry = zone("UP")
    short_entry = zone("DOWN")

    assert (long_entry.proximal, long_entry.distal) == (Decimal(104), Decimal(100))
    assert (short_entry.proximal, short_entry.distal) == (Decimal(100), Decimal(104))

    # The mid is the same price either way -- it is the band's middle, and
    # §12.4 measures R from it.
    assert long_entry.mid == short_entry.mid == Decimal(102)


def test_a_sweep_thesis_is_invalidated_at_the_swept_extreme() -> None:
    """§15.2: "zone distal edge / swept extreme per archetype".

    A1's chain is "external sweep -> MSS -> retest" and A5's is "sweep of
    range extreme -> rejection". What kills either is price returning through
    the level that was swept, not the retest zone failing -- the MSS-origin
    zone can sit far inside the swing that was taken.
    """
    for archetype in (Archetype.SWEEP_REVERSAL, Archetype.RANGE_LIQUIDITY_PLAY):
        found = invalidation_for(
            archetype,
            entry=zone(),
            swept_extreme=Decimal(96),
        )

        assert found is not None
        assert found.price == Decimal(96)
        assert found.rule == SWEPT_EXTREME


def test_a_zone_thesis_is_invalidated_at_the_distal_edge() -> None:
    """A2, A3 and A4 are zone theses, and §5's grammar already says a zone
    fails on a close beyond its distal edge."""

    for archetype in (
        Archetype.BREAKER_RETEST,
        Archetype.CONTINUATION_PULLBACK,
        Archetype.FVG_CONTINUATION,
    ):
        found = invalidation_for(archetype, entry=zone(), swept_extreme=None)

        assert found is not None
        assert found.price == Decimal(100)
        assert found.rule == ZONE_DISTAL_EDGE


def test_a_sweep_thesis_without_a_swept_extreme_has_no_invalidation() -> None:
    """None, rather than quietly falling back to the zone edge.

    §15.3 requires every payload field non-null, so this is a signal that
    cannot publish -- which is the honest outcome. Substituting the zone edge
    would hand an A1 a stop that has nothing to do with its thesis.
    """
    assert invalidation_for(Archetype.SWEEP_REVERSAL, entry=zone(), swept_extreme=None) is None


def test_r_is_measured_from_the_entry_mid() -> None:
    """§12.4: "R = |entry mid - invalidation|"."""

    assert levels().r_unit == Decimal(4)


def test_reward_is_measured_to_the_near_edge_of_the_target() -> None:
    """§12.3: "**touch** of target zone suffices".

    Touching the near edge is touching the pool, so measuring to the middle
    would understate every setup by half a pool's width and quietly fail
    §15.3's floor on setups that meet it.
    """
    long_levels = levels()

    # Entry mid 102, invalidation 98 => R = 4. Near edge of [112, 114] is 112.
    assert long_levels.primary_target.near_edge("UP") == Decimal(112)
    assert long_levels.r_multiple == Decimal("2.5")


def test_the_rr_floor_is_15_and_a_short_target_fails_it() -> None:
    """§15.3(3): "a structurally valid setup with no room to travel is not an
    opportunity"."""

    assert levels().meets_rr

    # R is still 4; the near edge at 107 gives 1.25.
    tight = levels(target_low="107", target_high="109")

    assert tight.r_multiple == Decimal("1.25")
    assert not tight.meets_rr
    assert Decimal("1.5") == MIN_RR


def test_a_zero_r_yields_no_multiple_rather_than_infinity() -> None:
    """An invalidation on the entry mid is a broken level pair, not a tight stop.

    Dividing by it would produce an R-multiple that sails through §15.3's
    floor on a signal with no stop at all.
    """
    broken = levels(invalidation_price="102")

    assert broken.r_unit == Decimal(0)
    assert broken.r_multiple is None
    assert not broken.meets_rr


def test_coherence_requires_the_target_and_stop_on_opposite_sides() -> None:
    """§15.3(1): "entry != invalidation side, target beyond entry in D"."""

    assert levels().coherent

    # A long whose target sits below the entry.
    assert not levels(target_low="90", target_high="92").coherent

    # A long whose invalidation sits above the entry.
    assert not levels(invalidation_price="110").coherent


def test_coherence_mirrors_for_a_short() -> None:
    from scanner.domain.confluence.levels import Invalidation

    short = SignalLevels(
        direction="DOWN",
        entry=zone("DOWN"),
        invalidation=Invalidation(Decimal(108), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(90), high=Decimal(92)),
    )

    assert short.coherent
    # Near edge for a short is the high of the band -- the first price reached
    # on the way down.
    assert short.primary_target.near_edge("DOWN") == Decimal(92)
    assert short.r_unit == Decimal(6)


def test_an_inverted_band_is_refused() -> None:
    with pytest.raises(ValueError, match="band_high must be"):
        entry_zone(
            zone_id="z",
            direction="UP",
            band_low=Decimal(104),
            band_high=Decimal(100),
        )
