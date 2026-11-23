"""The depth-snapshot freshness behind the Bundle D alarm (owner M2 Q3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prometheus_client import CollectorRegistry

from scanner.application.marketdata.depth_freshness import STALE_AFTER, depth_freshness
from scanner.infrastructure.observability.depth_metrics import (
    OLDEST_LATEST,
    STALE_SYMBOLS,
    DepthFreshnessMetrics,
)

NOW = datetime(2026, 11, 23, 6, tzinfo=UTC)
TODAY = datetime(2026, 11, 23, tzinfo=UTC)


def test_the_stalest_newest_snapshot_is_reported() -> None:
    latest = {"A": TODAY, "B": TODAY - timedelta(days=3), "C": TODAY - timedelta(days=1)}

    result = depth_freshness(latest, ["A", "B", "C"], NOW)

    assert result.oldest_latest_stamp == TODAY - timedelta(days=3)
    assert result.stale_symbols == 1
    assert result.symbols_with_snapshots == 3


def test_only_observable_symbols_count() -> None:
    latest = {"A": TODAY, "DELISTED": TODAY - timedelta(days=30)}

    result = depth_freshness(latest, ["A"], NOW)

    assert result.oldest_latest_stamp == TODAY
    assert result.stale_symbols == 0


def test_a_symbol_without_any_snapshot_is_left_out() -> None:
    # Newly listed: §6.4 already treats it as an incomplete check.
    result = depth_freshness({"A": TODAY}, ["A", "NEW"], NOW)

    assert result.symbols_with_snapshots == 1
    assert result.oldest_latest_stamp == TODAY


def test_exactly_two_days_is_not_yet_stale() -> None:
    stamp = NOW - STALE_AFTER

    assert depth_freshness({"A": stamp}, ["A"], NOW).stale_symbols == 0
    assert depth_freshness({"A": stamp - timedelta(seconds=1)}, ["A"], NOW).stale_symbols == 1


def test_no_snapshot_anywhere_has_no_oldest_stamp() -> None:
    result = depth_freshness({}, ["A"], NOW)

    assert result.oldest_latest_stamp is None
    assert result.stale_symbols == 0


def test_the_gauges_carry_a_timestamp_not_an_age() -> None:
    registry = CollectorRegistry()
    metrics = DepthFreshnessMetrics(registry)
    stamp = TODAY - timedelta(days=3)

    metrics.publish(depth_freshness({"A": stamp, "B": TODAY}, ["A", "B"], NOW))

    assert registry.get_sample_value(OLDEST_LATEST) == stamp.timestamp()
    assert registry.get_sample_value(STALE_SYMBOLS) == 1


def test_no_snapshot_leaves_the_timestamp_unset_rather_than_zero() -> None:
    registry = CollectorRegistry()
    metrics = DepthFreshnessMetrics(registry)

    metrics.publish(depth_freshness({}, ["A"], NOW))

    # Absent, so the absent() rule can report it; a 0 would read as 1970.
    assert registry.get_sample_value(OLDEST_LATEST) is None
    assert registry.get_sample_value(STALE_SYMBOLS) == 0


def test_a_later_publish_updates_the_same_gauge() -> None:
    registry = CollectorRegistry()
    metrics = DepthFreshnessMetrics(registry)

    metrics.publish(depth_freshness({"A": TODAY - timedelta(days=1)}, ["A"], NOW))
    metrics.publish(depth_freshness({"A": TODAY}, ["A"], NOW))

    assert registry.get_sample_value(OLDEST_LATEST) == TODAY.timestamp()
