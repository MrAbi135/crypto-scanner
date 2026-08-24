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


def test_process_info_may_be_set_twice_on_one_registry() -> None:
    """`bootstrap` is the one function every entrypoint runs.

    A duplicate `Gauge` registration raises, so without this a second call --
    two services in one test process, or a future re-bootstrap -- would take
    the process down at start-up.
    """
    registry = CollectorRegistry()

    set_process_info("api", "0.1.0", registry=registry)
    set_process_info("engine", "0.2.0", registry=registry)

    exposition = generate_latest(registry).decode()

    # Both label sets survive: the second call must not be a silent no-op
    # either, or a process would report the wrong release forever.
    assert 'scanner_process_info{process="api",version="0.1.0"} 1.0' in exposition
    assert 'scanner_process_info{process="engine",version="0.2.0"} 1.0' in exposition
