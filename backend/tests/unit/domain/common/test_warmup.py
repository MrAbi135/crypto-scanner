"""Tests for the SLS §1.9 warm-up gate."""

from __future__ import annotations

import pytest

from scanner.domain.common import (
    BASELINE_DAYS,
    DETECTION_MIN_CANDLES,
    LISTING_MIN_DAYS,
    VOLUME_MOMENTUM_MIN_CANDLES,
    WarmupCapability,
    detection_is_warm,
    is_warm,
    minimum_candles,
    uses_seasonal_baseline,
)
from scanner.domain.common.warmup import required_warmup_candles
from scanner.shared import Timeframe


def test_thresholds_match_the_specification() -> None:
    """§1.9's table, transcribed. A silent drift here weakens every engine."""

    assert DETECTION_MIN_CANDLES == 300
    assert VOLUME_MOMENTUM_MIN_CANDLES == 100
    assert LISTING_MIN_DAYS == 14


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (WarmupCapability.DETECTION, 300),
        (WarmupCapability.VOLUME, 100),
        (WarmupCapability.MOMENTUM, 100),
    ],
)
def test_each_capability_carries_its_own_floor(
    capability: WarmupCapability,
    expected: int,
) -> None:
    assert minimum_candles(capability) == expected


@pytest.mark.parametrize(
    ("closed_candles", "expected"),
    [
        (0, False),
        (299, False),
        (300, True),
        (301, True),
        (10_000, True),
    ],
)
def test_the_detection_floor_is_inclusive(closed_candles: int, expected: bool) -> None:
    """§1.9 says "≥ 300 closed candles", so exactly 300 is warm.

    Worth pinning: an off-by-one here either analyses a series doctrine
    excludes, or refuses one it permits, and neither is visible from the
    outside.
    """

    assert detection_is_warm(closed_candles) is expected


def test_volume_and_momentum_warm_earlier_than_detection() -> None:
    """The floors differ on purpose; a single shared constant would be wrong."""

    assert is_warm(WarmupCapability.VOLUME, closed_candles=100) is True
    assert is_warm(WarmupCapability.MOMENTUM, closed_candles=100) is True
    assert is_warm(WarmupCapability.DETECTION, closed_candles=100) is False


def test_a_negative_count_is_rejected_rather_than_treated_as_cold() -> None:
    """Returning False would mask a caller bug as an ordinary warm-up miss."""

    with pytest.raises(ValueError, match="closed_candles must be non-negative"):
        detection_is_warm(-1)


class TestRequiredWarmupCandles:
    """§1.9's floor is not the whole requirement.

    §2.11 makes the RVOL baseline time-of-day aware on the fast timeframes: it
    compares against the same slot on each of the previous 20 days, so those
    rungs need twenty *days* of history, not twenty candles. A single flat
    count across the ladder — the engine used 600 — leaves M5 and M15 unable to
    score for about three weeks while every other signal says they are warm.
    """

    def test_a_seasonal_timeframe_needs_twenty_days_not_the_floor(self) -> None:
        # 20 days x 288 five-minute candles.
        assert required_warmup_candles(Timeframe.M5) == 5760
        assert required_warmup_candles(Timeframe.M15) == 1920

    def test_the_floor_still_applies_where_it_is_the_larger_number(self) -> None:
        """H1's 20 days is 480 candles, under §1.9's 300? No -- over it.

        H4 is not seasonal at all, so nothing raises it above the floor.
        """
        assert required_warmup_candles(Timeframe.H1) == 480
        assert required_warmup_candles(Timeframe.H4) == DETECTION_MIN_CANDLES

    def test_no_timeframe_is_allowed_below_the_detection_gate(self) -> None:
        for timeframe in Timeframe:
            assert required_warmup_candles(timeframe) >= DETECTION_MIN_CANDLES

    def test_every_seasonal_timeframe_covers_its_own_baseline(self) -> None:
        """The check that would have caught the original defect.

        Written against the RVOL module's own constants rather than the numbers
        above, so changing BASELINE_DAYS or the seasonal set cannot leave this
        agreeing with a stale expectation.
        """
        for timeframe in Timeframe:
            if not uses_seasonal_baseline(timeframe):
                continue

            days_covered = required_warmup_candles(timeframe) * timeframe.minutes / (24 * 60)

            assert days_covered >= BASELINE_DAYS
