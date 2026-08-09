"""Unit tests for market-data freshness tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scanner.application.marketdata.freshness import (
    FreshnessState,
    FreshnessTracker,
)
from scanner.shared import Timeframe


def make_tracker() -> FreshnessTracker:
    return FreshnessTracker(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
    )


def test_fresh_event_stays_fresh() -> None:
    tracker = make_tracker()

    event_at = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    observed_at = event_at + timedelta(seconds=1)

    state = tracker.observe_event(
        event_at=event_at,
        observed_at=observed_at,
    )

    assert state is FreshnessState.FRESH
    assert tracker.detection_allowed is True


def test_event_over_five_seconds_becomes_stale() -> None:
    tracker = make_tracker()

    event_at = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    observed_at = event_at + timedelta(seconds=6)

    state = tracker.observe_event(
        event_at=event_at,
        observed_at=observed_at,
    )

    assert state is FreshnessState.STALE
    assert tracker.detection_allowed is False


def test_event_over_thirty_seconds_becomes_degraded() -> None:
    tracker = make_tracker()

    event_at = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    observed_at = event_at + timedelta(seconds=31)

    state = tracker.observe_event(
        event_at=event_at,
        observed_at=observed_at,
    )

    assert state is FreshnessState.DEGRADED
    assert tracker.detection_allowed is False


def test_future_event_timestamp_becomes_suspect() -> None:
    tracker = make_tracker()

    observed_at = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    event_at = observed_at + timedelta(seconds=1)

    state = tracker.observe_event(
        event_at=event_at,
        observed_at=observed_at,
    )

    assert state is FreshnessState.SUSPECT
    assert tracker.detection_allowed is False


def test_mark_suspect_resets_recovery_counter() -> None:
    tracker = make_tracker()
    tracker.recovery_candles = 5

    state = tracker.mark_suspect()

    assert state is FreshnessState.SUSPECT
    assert tracker.recovery_candles == 0


def test_mark_degraded_resets_recovery_counter() -> None:
    tracker = make_tracker()
    tracker.recovery_candles = 5

    state = tracker.mark_degraded()

    assert state is FreshnessState.DEGRADED
    assert tracker.recovery_candles == 0


def test_degraded_state_recovers_after_twenty_verified_candles() -> None:
    tracker = make_tracker()
    tracker.mark_degraded()

    for _ in range(19):
        state = tracker.record_verified_candle()

    assert state is FreshnessState.DEGRADED
    assert tracker.recovery_candles == 19

    state = tracker.record_verified_candle()

    assert state is FreshnessState.FRESH
    assert tracker.recovery_candles == 0
    assert tracker.detection_allowed is True


def test_suspect_state_recovers_after_twenty_verified_candles() -> None:
    tracker = make_tracker()
    tracker.mark_suspect()

    for _ in range(20):
        state = tracker.record_verified_candle()

    assert state is FreshnessState.FRESH
    assert tracker.recovery_candles == 0


def test_break_recovery_resets_streak() -> None:
    tracker = make_tracker()
    tracker.mark_degraded()

    for _ in range(7):
        tracker.record_verified_candle()

    tracker.break_recovery()

    assert tracker.state is FreshnessState.DEGRADED
    assert tracker.recovery_candles == 0


def test_verified_candle_does_not_change_normal_state() -> None:
    tracker = make_tracker()

    state = tracker.record_verified_candle()

    assert state is FreshnessState.FRESH
    assert tracker.recovery_candles == 0
