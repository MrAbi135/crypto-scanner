"""SLS §14's collectors."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest

from scanner.infrastructure.observability.detection_metrics import (
    PASS_BUCKETS,
    PrometheusDetectionMetrics,
)
from scanner.infrastructure.observability.metrics import LATENCY_BUCKETS


def test_the_pass_histogram_can_express_the_targets_it_is_measured_against() -> None:
    """The governed buckets stop at 10 s and §14's cycle target is 30 s.

    A pass measured at 104 s on the soak VM would land in `+Inf` under
    `LATENCY_BUCKETS`, the p95 would read ">10 s" forever, and the metric
    could not show the improvement it exists to track.
    """
    assert max(LATENCY_BUCKETS) < 30.0

    # Each §14 target sits on a boundary, so a quantile lands where the
    # doctrine reads rather than between two buckets.
    for target in (2.0, 30.0, 60.0):
        assert target in PASS_BUCKETS

    # And the tail runs past the 104 s actually measured, so a regression
    # beyond it is still visible instead of saturating.
    assert max(PASS_BUCKETS) > 104.0


def test_a_pass_lands_in_the_bucket_for_its_timeframe() -> None:
    registry = CollectorRegistry()
    metrics = PrometheusDetectionMetrics(registry=registry)

    metrics.observe_pass(1.5, symbol="BTCUSDT", timeframe="H1")
    metrics.observe_pass(45.0, symbol="ETHUSDT", timeframe="H1")
    metrics.observe_pass(0.2, symbol="BTCUSDT", timeframe="H4")

    exposition = generate_latest(registry).decode()

    assert 'scanner_detection_pass_seconds_count{timeframe="H1"} 2.0' in exposition
    assert 'scanner_detection_pass_seconds_count{timeframe="H4"} 1.0' in exposition
    # 1.5 s is inside the 2 s target; 45 s is not.
    assert 'scanner_detection_pass_seconds_bucket{le="2.0",timeframe="H1"} 1.0' in exposition
    assert 'scanner_detection_pass_seconds_bucket{le="60.0",timeframe="H1"} 2.0' in exposition


def test_the_symbol_is_not_a_label() -> None:
    """~400 symbols by 5 timeframes by 9 buckets is 18,000 series.

    §14 states its targets per timeframe, and the symbol is already on the
    structured log for the pass -- where a one-off question can find it
    without paying for it on every scrape.
    """
    registry = CollectorRegistry()
    metrics = PrometheusDetectionMetrics(registry=registry)

    metrics.observe_pass(1.0, symbol="BTCUSDT", timeframe="H1")

    assert "BTCUSDT" not in generate_latest(registry).decode()


def test_the_funnel_counts_each_outcome_under_one_metric() -> None:
    """§14 wants the candidate-to-published *ratio*.

    A ratio wants one series divided by another with the same label set, which
    two separate metrics could not give.
    """
    registry = CollectorRegistry()
    metrics = PrometheusDetectionMetrics(registry=registry)

    metrics.record_publication("published", timeframe="H1")
    metrics.record_publication("DUPLICATE_KEY", timeframe="H1")
    metrics.record_publication("DUPLICATE_KEY", timeframe="H1")
    metrics.record_publication("refreshed", timeframe="H4")

    exposition = generate_latest(registry).decode()

    assert (
        'scanner_detection_publications_total{outcome="published",timeframe="H1"} 1.0' in exposition
    )
    assert (
        'scanner_detection_publications_total{outcome="DUPLICATE_KEY",timeframe="H1"} 2.0'
        in exposition
    )
    assert (
        'scanner_detection_publications_total{outcome="refreshed",timeframe="H4"} 1.0' in exposition
    )
