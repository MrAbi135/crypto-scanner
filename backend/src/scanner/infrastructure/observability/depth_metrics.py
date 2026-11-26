"""Prometheus gauges for the depth snapshots §6.4 reads (owner M2 Q3).

See `application.marketdata.depth_freshness` for why the alarm compares a
timestamp with `time()` instead of reading an age.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Gauge

from scanner.application.marketdata.depth_freshness import DepthFreshness

OLDEST_LATEST = "scanner_depth_snapshot_oldest_latest_timestamp_seconds"
STALE_SYMBOLS = "scanner_depth_snapshot_stale_symbols"


class DepthFreshnessMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self._registry = registry
        # Created on the first real stamp, not here: an unlabelled Gauge
        # exports 0 from the moment it exists, which would read as a 1970
        # stamp (firing the age rule with a nonsense value) and would hide the
        # absent() rule for a worker that never published.
        self._oldest: Gauge | None = None
        self._stale = Gauge(
            STALE_SYMBOLS,
            "Observable symbols whose newest depth snapshot is older than two days",
            registry=registry,
        )

    def publish(self, freshness: DepthFreshness) -> None:
        # No symbol with a snapshot leaves the timestamp unset, which the
        # absent() rule reports; a zero would read as 1970 and fire the age
        # rule with a misleading value.
        if freshness.oldest_latest_stamp is not None:
            if self._oldest is None:
                self._oldest = Gauge(
                    OLDEST_LATEST,
                    "Newest depth snapshot stamp of the stalest observable symbol (unix seconds)",
                    registry=self._registry,
                )

            self._oldest.set(freshness.oldest_latest_stamp.timestamp())

        self._stale.set(freshness.stale_symbols)
