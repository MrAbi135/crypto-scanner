"""SLS v1.0.15 §12.3-§12.4: an ACTIVE laddered signal, walked from the entry.

The fixture is a long with the live shape: zone 100-104, so the proximal edge
is 104; a 1% risk stop at 102.96, so R = 1.04; and the owner's ladder, TP1 2R,
TP2 3R, TP3 4R -- 106.08, 107.12, 108.16. Each test is one way a trade ends.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    ZONE_DISTAL_EDGE,
    SignalLevels,
    TargetBand,
    TargetLadder,
    entry_zone,
    risk_stop_for,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import Candle, SignalState, accounting, observe, walk_ladder
from scanner.domain.lifecycle.state import TERMINAL_STATES, may_transition
from scanner.domain.lifecycle.track_record import GroupStats

LADDER = TargetLadder(Decimal(2), Decimal(1))


def laddered(direction: str = "UP") -> SignalLevels:
    e = entry_zone(zone_id="z1", direction=direction, band_low=Decimal(100), band_high=Decimal(104))

    return SignalLevels(
        direction=direction,
        entry=e,
        invalidation=risk_stop_for(entry=e, direction=direction, stop_pct=Decimal("1.0")),
        primary_target=TargetBand(low=Decimal(120), high=Decimal(120), pool_id="p1"),
        ladder=LADDER,
    )


def c(high: str, low: str, close: str) -> Candle:
    return Candle(high=Decimal(high), low=Decimal(low), close=Decimal(close))


def walk(candles, *, levels=None, elapsed=1, ttl=48):
    return walk_ladder(
        candles,
        levels=levels or laddered(),
        elapsed_candles=elapsed,
        ttl_candles=ttl,
    )


# --- the four ways out -------------------------------------------------------


def test_a_touch_of_the_initial_stop_before_tp1_is_a_one_r_loss() -> None:
    result = walk([c("104.5", "102.9", "103")])

    assert result.to_state is SignalState.FAILED
    assert result.realised_r == Decimal(-1)
    assert result.rungs == 0


def test_tp1_then_back_to_the_entry_is_closed_flat() -> None:
    """The case CLOSED_FLAT exists for: risk removed, move given back."""
    result = walk([c("106.5", "104.5", "106"), c("105", "103.9", "104")])

    assert result.to_state is SignalState.CLOSED_FLAT
    assert result.realised_r == Decimal(0)
    assert result.rungs == 1


def test_tp2_then_back_to_tp1_books_two_r() -> None:
    result = walk([c("107.5", "104.5", "107"), c("107", "106", "106.2")])

    assert result.to_state is SignalState.SUCCESS
    assert result.realised_r == Decimal(2), "TP2 moves the stop to TP1, which is 2R"
    assert result.rungs == 2


def test_a_candle_that_climbs_several_rungs_raises_the_stop_through_all_of_them() -> None:
    """TP3 (108.16) reached in one bar, so the stop sits at TP2 = 3R."""
    result = walk([c("109", "104.5", "108.5"), c("108.5", "107", "107.2")])

    assert result.rungs == 3
    assert result.to_state is SignalState.SUCCESS
    assert result.realised_r == Decimal(3)


def test_the_ladder_has_no_ceiling_to_stop_at() -> None:
    """Price far above every rung the owner named still just trails the stop.
    No TP closes the trade -- there is no last one."""
    result = walk([c("130", "104.5", "129")])

    assert result.to_state is None, "a rung is never an exit, only a stop move"
    assert result.rungs > 20


# --- the boundaries ----------------------------------------------------------


def test_one_candle_holding_the_stop_and_the_next_rung_resolves_to_the_stop() -> None:
    """v1.0.8's rule: never the favourable reading of an unknowable order."""
    result = walk([c("106.5", "102.9", "105")])

    assert result.to_state is SignalState.FAILED
    assert result.realised_r == Decimal(-1)
    assert "order indeterminate" in result.reason


def test_the_ttl_marks_a_live_trade_at_its_final_close() -> None:
    result = walk([c("105", "104", "104.52")], elapsed=48, ttl=48)

    assert result.to_state is SignalState.EXPIRED_ACTIVE
    assert result.realised_r == Decimal("0.52") / Decimal("1.04")


def test_a_stop_on_the_ttl_candle_is_a_resolution_not_an_expiry() -> None:
    """The stop is read before the clock, as `observe` reads the target."""
    result = walk([c("104.5", "102.9", "103")], elapsed=48, ttl=48)

    assert result.to_state is SignalState.FAILED


def test_a_live_trade_inside_its_ttl_changes_nothing() -> None:
    result = walk([c("105.5", "103.5", "105")])

    assert result.to_state is None
    assert result.realised_r is None


def test_a_short_mirrors_a_long() -> None:
    """Proximal 100, stop 101, R = 1: TP1 98 reached, then back to 100."""
    result = walk(
        [c("100", "97.5", "98"), c("100.2", "98", "99")],
        levels=laddered("DOWN"),
    )

    assert result.rungs == 1
    assert result.to_state is SignalState.CLOSED_FLAT


def test_a_pool_exit_signal_cannot_be_walked() -> None:
    e = entry_zone(zone_id="z1", direction="UP", band_low=Decimal(100), band_high=Decimal(104))
    pool_exit = SignalLevels(
        direction="UP",
        entry=e,
        invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(120), high=Decimal(120)),
    )

    with pytest.raises(ValueError):
        walk([c("105", "104", "104.5")], levels=pool_exit)


# --- observe() and the state machine -----------------------------------------


def test_observe_refuses_an_active_laddered_signal() -> None:
    """A monitor that forgot to route it must fail loudly, not watch the pool."""
    with pytest.raises(ValueError):
        observe(
            SignalState.ACTIVE,
            c("105", "104", "104.5"),
            levels=laddered(),
            elapsed_candles=1,
            ttl_candles=48,
        )


def test_observe_still_activates_a_laddered_signal_on_the_entry_touch() -> None:
    """The entry is unchanged by v1.0.15; only what follows it is."""
    result = observe(
        SignalState.PUBLISHED,
        c("106", "103.9", "105"),
        levels=laddered(),
        elapsed_candles=1,
        ttl_candles=48,
    )

    assert result.to_state is SignalState.ACTIVE


def test_closed_flat_is_terminal_and_reachable_only_from_active() -> None:
    assert SignalState.CLOSED_FLAT in TERMINAL_STATES
    assert may_transition(SignalState.ACTIVE, SignalState.CLOSED_FLAT)
    assert not may_transition(SignalState.PUBLISHED, SignalState.CLOSED_FLAT)


# --- §12.4: reported, never rated --------------------------------------------


def test_a_closed_flat_outcome_does_not_count_toward_the_hit_rate() -> None:
    book = accounting(
        SignalState.CLOSED_FLAT,
        levels=laddered(),
        candles=[c("106.5", "103.9", "104")],
        realised_r=Decimal(0),
    )

    assert book.realised_r == Decimal(0)
    assert not book.counts_toward_hit_rate


def test_closed_flat_is_resolved_but_never_rated() -> None:
    stats = GroupStats(successes=3, failures=1, expired=0, invalidated=0, closed_flat=2)

    assert stats.resolved == 6, "a CLOSED_FLAT that vanished from `resolved` is the defect"
    assert stats.hit_rate.rated == 4, "and one that entered the rate is the flattery"
