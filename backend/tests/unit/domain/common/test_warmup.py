"""Tests for the SLS §1.9 warm-up gate."""

from __future__ import annotations

import pytest

from scanner.domain.common import (
    DETECTION_MIN_CANDLES,
    LISTING_MIN_DAYS,
    VOLUME_MOMENTUM_MIN_CANDLES,
    WarmupCapability,
    detection_is_warm,
    is_warm,
    minimum_candles,
)


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
