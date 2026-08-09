"""Unit tests for S3 universe tier classification."""

from __future__ import annotations

from decimal import Decimal

from scanner.application.marketdata.universe import (
    LiquiditySnapshot,
    UniverseTier,
    alert_eligible,
    classify_tier,
    scanned_timeframes,
)
from scanner.shared import Timeframe


def snapshot(
    *,
    volume: str,
    spread: str,
    depth: str,
) -> LiquiditySnapshot:
    return LiquiditySnapshot(
        median_daily_quote_volume=Decimal(volume),
        median_spread_bps=Decimal(spread),
        median_depth_2pct=Decimal(depth),
    )


def test_t1_exact_boundary_is_t1() -> None:
    result = classify_tier(
        snapshot(
            volume="100000000",
            spread="2",
            depth="1000000",
        )
    )

    assert result is UniverseTier.T1


def test_t2_exact_boundary_is_t2() -> None:
    result = classify_tier(
        snapshot(
            volume="20000000",
            spread="5",
            depth="250000",
        )
    )

    assert result is UniverseTier.T2


def test_t3_exact_boundary_is_t3() -> None:
    result = classify_tier(
        snapshot(
            volume="5000000",
            spread="10",
            depth="100000",
        )
    )

    assert result is UniverseTier.T3


def test_missing_any_t1_requirement_falls_to_lower_tier() -> None:
    result = classify_tier(
        snapshot(
            volume="150000000",
            spread="3",
            depth="2000000",
        )
    )

    assert result is UniverseTier.T2


def test_missing_any_t2_requirement_falls_to_t3() -> None:
    result = classify_tier(
        snapshot(
            volume="50000000",
            spread="7",
            depth="500000",
        )
    )

    assert result is UniverseTier.T3


def test_below_t3_on_one_metric_is_ineligible() -> None:
    result = classify_tier(
        snapshot(
            volume="10000000",
            spread="8",
            depth="90000",
        )
    )

    assert result is UniverseTier.INELIGIBLE


def test_t1_scans_all_supported_timeframes() -> None:
    assert scanned_timeframes(UniverseTier.T1) == (
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
        Timeframe.W1,
    )


def test_t2_does_not_scan_m5() -> None:
    assert scanned_timeframes(UniverseTier.T2) == (
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
        Timeframe.W1,
    )


def test_t3_scans_h1_and_higher() -> None:
    assert scanned_timeframes(UniverseTier.T3) == (
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
        Timeframe.W1,
    )


def test_ineligible_symbol_has_no_scanned_timeframes() -> None:
    assert scanned_timeframes(UniverseTier.INELIGIBLE) == ()


def test_alert_eligibility() -> None:
    assert alert_eligible(UniverseTier.T1) is True
    assert alert_eligible(UniverseTier.T2) is True
    assert alert_eligible(UniverseTier.T3) is True
    assert alert_eligible(UniverseTier.INELIGIBLE) is False
