"""Unit tests for S3 universe tier hysteresis."""

from __future__ import annotations

from scanner.application.marketdata.universe import UniverseTier
from scanner.application.marketdata.universe_state import UniverseTierState


def test_promotion_requires_seven_consecutive_days() -> None:
    state = UniverseTierState()

    for _ in range(6):
        result = state.evaluate(UniverseTier.T3)

    assert result is UniverseTier.INELIGIBLE
    assert state.current_tier is UniverseTier.INELIGIBLE
    assert state.consecutive_passes == 6

    result = state.evaluate(UniverseTier.T3)

    assert result is UniverseTier.T3
    assert state.current_tier is UniverseTier.T3
    assert state.candidate_tier is None
    assert state.consecutive_passes == 0


def test_demotion_requires_three_consecutive_days() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T1,
    )

    for _ in range(2):
        result = state.evaluate(UniverseTier.T2)

    assert result is UniverseTier.T1
    assert state.current_tier is UniverseTier.T1
    assert state.consecutive_failures == 2

    result = state.evaluate(UniverseTier.T2)

    assert result is UniverseTier.T2
    assert state.current_tier is UniverseTier.T2
    assert state.candidate_tier is None
    assert state.consecutive_failures == 0


def test_matching_current_tier_resets_candidate_state() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T2,
    )

    state.evaluate(UniverseTier.T1)
    state.evaluate(UniverseTier.T1)

    assert state.consecutive_passes == 2

    result = state.evaluate(UniverseTier.T2)

    assert result is UniverseTier.T2
    assert state.candidate_tier is None
    assert state.consecutive_passes == 0
    assert state.consecutive_failures == 0


def test_changed_promotion_candidate_restarts_pass_streak() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.INELIGIBLE,
    )

    for _ in range(4):
        state.evaluate(UniverseTier.T3)

    result = state.evaluate(UniverseTier.T2)

    assert result is UniverseTier.INELIGIBLE
    assert state.candidate_tier is UniverseTier.T2
    assert state.consecutive_passes == 1


def test_changed_demotion_candidate_restarts_failure_streak() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T1,
    )

    state.evaluate(UniverseTier.T2)
    state.evaluate(UniverseTier.T2)

    result = state.evaluate(UniverseTier.T3)

    assert result is UniverseTier.T1
    assert state.candidate_tier is UniverseTier.T3
    assert state.consecutive_failures == 1


def test_promotion_from_t3_to_t1_requires_seven_t1_days() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T3,
    )

    for _ in range(6):
        result = state.evaluate(UniverseTier.T1)

    assert result is UniverseTier.T3

    result = state.evaluate(UniverseTier.T1)

    assert result is UniverseTier.T1
    assert state.current_tier is UniverseTier.T1


def test_demotion_to_ineligible_requires_three_failures() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T3,
    )

    state.evaluate(UniverseTier.INELIGIBLE)
    state.evaluate(UniverseTier.INELIGIBLE)

    assert state.current_tier is UniverseTier.T3

    result = state.evaluate(UniverseTier.INELIGIBLE)

    assert result is UniverseTier.INELIGIBLE
    assert state.current_tier is UniverseTier.INELIGIBLE


def test_promotion_streak_resets_when_observed_tier_returns_to_current() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T2,
    )

    for _ in range(5):
        state.evaluate(UniverseTier.T1)

    assert state.consecutive_passes == 5

    state.evaluate(UniverseTier.T2)

    assert state.consecutive_passes == 0
    assert state.candidate_tier is None


def test_demotion_streak_resets_when_observed_tier_returns_to_current() -> None:
    state = UniverseTierState(
        current_tier=UniverseTier.T1,
    )

    state.evaluate(UniverseTier.T2)
    state.evaluate(UniverseTier.T2)

    assert state.consecutive_failures == 2

    state.evaluate(UniverseTier.T1)

    assert state.consecutive_failures == 0
    assert state.candidate_tier is None
