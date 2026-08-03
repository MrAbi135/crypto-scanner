"""Metrics factory tests (S0.3 §8.2)."""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from scanner.infrastructure.observability.metrics import counter, histogram, set_process_info


def test_name_must_have_scanner_prefix() -> None:
    with pytest.raises(ValueError, match="scanner_"):
        counter("bad_name", "doc", registry=CollectorRegistry())


def test_name_must_be_lowercase() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        counter("scanner_Bad", "doc", registry=CollectorRegistry())


def test_counter_histogram_and_process_info_register() -> None:
    registry = CollectorRegistry()
    counter("scanner_test_events", "test events", ["kind"], registry=registry).labels(
        kind="x"
    ).inc()
    histogram("scanner_test_latency_seconds", "test latency", registry=registry).observe(0.02)
    set_process_info("api", "0.1.0", registry=registry)

    exposition = generate_latest(registry).decode()
    assert "scanner_test_events_total" in exposition
    assert "scanner_test_latency_seconds_bucket" in exposition
    assert 'scanner_process_info{process="api",version="0.1.0"}' in exposition
