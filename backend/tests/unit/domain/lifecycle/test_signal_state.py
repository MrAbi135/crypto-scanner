"""§12's state machine and its per-candle monitoring."""

from __future__ import annotations

from decimal import Decimal

from scanner.domain.confluence import (
    ZONE_DISTAL_EDGE,
    SignalLevels,
    TargetBand,
    entry_zone,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import (
    TERMINAL_STATES,
    Candle,
    SignalState,
    may_transition,
    observe,
)

# A long: entry band [100, 104], invalidation 98, target [112, 114].
LONG = SignalLevels(
    direction="UP",
    entry=entry_zone(zone_id="z1", direction="UP", band_low=Decimal(100), band_high=Decimal(104)),
    invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
    primary_target=TargetBand(low=Decimal(112), high=Decimal(114)),
)

SHORT = SignalLevels(
    direction="DOWN",
    entry=entry_zone(zone_id="z2", direction="DOWN", band_low=Decimal(100), band_high=Decimal(104)),
    invalidation=Invalidation(Decimal(106), ZONE_DISTAL_EDGE),
    primary_target=TargetBand(low=Decimal(90), high=Decimal(92)),
)


def candle(high: str, low: str, close: str) -> Candle:
    return Candle(high=Decimal(high), low=Decimal(low), close=Decimal(close))


def look(state, c, *, levels=LONG, elapsed=0, ttl=24, premise_broken=False):
    return observe(
        state,
        c,
        levels=levels,
        elapsed_candles=elapsed,
        ttl_candles=ttl,
        premise_broken=premise_broken,
    )


def test_only_the_edges_12_draws_are_allowed() -> None:
    """The diagram, transcribed. A dict rather than a chain of ifs so an edge
    nobody drew cannot be taken by accident."""

    assert may_transition(SignalState.DETECTED, SignalState.PUBLISHED)
    assert may_transition(SignalState.PUBLISHED, SignalState.ACTIVE)
    assert may_transition(SignalState.ACTIVE, SignalState.SUCCESS)

    # §12.3: INVALIDATED_EARLY is "pre-touch only", so ACTIVE cannot reach it.
    assert not may_transition(SignalState.ACTIVE, SignalState.INVALIDATED_EARLY)

    # No resurrection from a terminal state.
    for terminal in TERMINAL_STATES:
        assert not may_transition(terminal, SignalState.ACTIVE)


def test_a_terminal_signal_is_not_moved_by_any_candle() -> None:
    for terminal in TERMINAL_STATES:
        assert look(terminal, candle("200", "1", "150")).to_state is None


def test_touching_the_entry_band_activates_it() -> None:
    """§12.3's "entry-zone touch check".

    A touch is the range reaching the band, not the close being inside it --
    price that traded into the zone and left has still filled an order resting
    there.
    """
    # Closes well above the band, but wicked into it.
    assert look(SignalState.PUBLISHED, candle("120", "103", "118")).to_state is (SignalState.ACTIVE)


def test_a_wick_through_the_invalidation_is_a_stress_test_not_a_failure() -> None:
    """§12.3: "wick-through alone records `stress_test: true` but does not fail
    the signal; consistent with zone grammar §5.9".

    A scanner that failed on wicks would be stopped out by every liquidity
    grab it exists to detect.
    """
    observed = look(SignalState.ACTIVE, candle("105", "97", "101"))

    assert observed.to_state is None
    assert observed.stress_test


def test_a_close_beyond_the_invalidation_fails_it() -> None:
    observed = look(SignalState.ACTIVE, candle("105", "96", "97"))

    assert observed.to_state is SignalState.FAILED
    # The close is beyond, so this is not the wick case.
    assert not observed.stress_test


def test_a_touch_of_the_target_succeeds() -> None:
    """§12.3: "touch of target zone suffices (targets are liquidity pools; a
    wick into the pool is the pool being consumed)"."""

    observed = look(SignalState.ACTIVE, candle("112", "105", "108"))

    assert observed.to_state is SignalState.SUCCESS


def test_one_candle_holding_both_outcomes_resolves_against_the_signal() -> None:
    """§12.4 awards SUCCESS when the target is touched *before* the
    invalidation close, and one candle cannot establish "before".

    §15.4 puts the platform's record above its numbers. Awarding the
    favourable reading of an unknowable order is exactly the flattery that
    ruins a track record, so it resolves to FAILED -- and the reason says the
    order was indeterminate, so the count stays auditable rather than merely
    conservative.
    """
    observed = look(SignalState.ACTIVE, candle("113", "96", "97"))

    assert observed.to_state is SignalState.FAILED
    assert "indeterminate" in observed.reason


def test_the_ttl_does_not_hide_a_resolution_on_the_same_candle() -> None:
    """Invalidation and target are read before the clock.

    A signal that resolved on the candle its TTL lapsed *resolved*, and §12.4
    counts it. Reading the clock first would file a real outcome as expired,
    and §12.4 excludes expired signals from the hit rate -- so the mistake
    would quietly delete the result rather than misreport it.
    """
    hit = look(SignalState.ACTIVE, candle("112", "105", "108"), elapsed=24, ttl=24)

    assert hit.to_state is SignalState.SUCCESS

    quiet = look(SignalState.ACTIVE, candle("108", "105", "106"), elapsed=24, ttl=24)

    assert quiet.to_state is SignalState.EXPIRED_ACTIVE


def test_an_untouched_signal_expires_into_its_own_state() -> None:
    """§12's diagram separates EXPIRED_UNTOUCHED from EXPIRED_ACTIVE.

    They mean different things: one is a setup the market never came back to,
    the other a position that ran out of time. Collapsing them would hide a
    target-selection problem inside an entry-selection one.
    """
    observed = look(SignalState.PUBLISHED, candle("120", "110", "118"), elapsed=24, ttl=24)

    assert observed.to_state is SignalState.EXPIRED_UNTOUCHED


def test_a_broken_premise_only_bites_before_the_entry_is_touched() -> None:
    """§12.3: "premise checks ... => INVALIDATED_EARLY (pre-touch only)".

    Once price is in the zone the premise has been acted on, and a late break
    is what FAILED is for.
    """
    early = look(SignalState.PUBLISHED, candle("108", "106", "107"), premise_broken=True)

    assert early.to_state is SignalState.INVALIDATED_EARLY

    late = look(SignalState.ACTIVE, candle("108", "105", "107"), premise_broken=True)

    assert late.to_state is None


def test_the_premise_check_outranks_an_entry_touch_on_the_same_candle() -> None:
    """A destroyed premise is not an entry worth taking.

    Activating first and invalidating later would put the signal through a
    state it was never eligible for, and §12.4 would then count it as a
    position rather than a setup that died.
    """
    observed = look(SignalState.PUBLISHED, candle("104", "100", "102"), premise_broken=True)

    assert observed.to_state is SignalState.INVALIDATED_EARLY


def test_every_rule_mirrors_for_a_short() -> None:
    assert look(SignalState.PUBLISHED, candle("101", "95", "97"), levels=SHORT).to_state is (
        SignalState.ACTIVE
    )

    stressed = look(SignalState.ACTIVE, candle("108", "100", "104"), levels=SHORT)

    assert stressed.to_state is None
    assert stressed.stress_test

    assert look(SignalState.ACTIVE, candle("109", "100", "108"), levels=SHORT).to_state is (
        SignalState.FAILED
    )

    assert look(SignalState.ACTIVE, candle("100", "92", "94"), levels=SHORT).to_state is (
        SignalState.SUCCESS
    )
