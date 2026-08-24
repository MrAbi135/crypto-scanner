"""Prometheus metrics foundation (TAD §25; S0.3 §8.2).

Factories that mint convention-compliant metrics so future metrics are born
correct: names are `scanner_<area>_<name>_<unit>`, latency histograms use one
governed bucket set (5ms → 10s). `process_info` carries per-process metadata.
Registering on the default registry means metrics surface at /internal/metrics.
"""

from __future__ import annotations

from collections.abc import Sequence

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

# Governed latency buckets in seconds, 5ms → 10s, log-spaced (TAD §25).
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _require_convention(name: str) -> None:
    if not name.startswith("scanner_"):
        raise ValueError(f"metric name must start with 'scanner_': {name!r}")
    if name != name.lower() or " " in name:
        raise ValueError(f"metric name must be lowercase snake_case: {name!r}")


# One gauge per registry, remembered. `Gauge(...)` raises on a duplicate name,
# so a second call would take the process down at boot -- and `bootstrap` is
# the one function every entrypoint runs. Held by registry object rather than
# a module flag so a test registry and the default one do not shadow each
# other.
_PROCESS_INFO: dict[CollectorRegistry, Gauge] = {}


def set_process_info(process: str, version: str, *, registry: CollectorRegistry = REGISTRY) -> None:
    gauge = _PROCESS_INFO.get(registry)

    if gauge is None:
        gauge = Gauge(
            "scanner_process_info", "Process metadata", ["process", "version"], registry=registry
        )

        _PROCESS_INFO[registry] = gauge

    gauge.labels(process=process, version=version).set(1)


def counter(
    name: str,
    documentation: str,
    labelnames: Sequence[str] = (),
    *,
    registry: CollectorRegistry = REGISTRY,
) -> Counter:
    _require_convention(name)
    return Counter(name, documentation, list(labelnames), registry=registry)


def histogram(
    name: str,
    documentation: str,
    labelnames: Sequence[str] = (),
    *,
    buckets: Sequence[float] = LATENCY_BUCKETS,
    registry: CollectorRegistry = REGISTRY,
) -> Histogram:
    _require_convention(name)
    return Histogram(
        name, documentation, list(labelnames), buckets=tuple(buckets), registry=registry
    )
