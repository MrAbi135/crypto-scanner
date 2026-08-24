"""Prometheus collectors behind SLS §14's targets.

**The governed latency buckets do not fit this.** `LATENCY_BUCKETS` stops at
10 s, which is right for the request latencies TAD §25 minted it for. A
detection pass has been measured at 104 s on the soak VM against a §14 target
of 30 s — every observation would land in `+Inf`, the p95 would read ">10 s"
forever, and the metric would be unable to show the improvement it exists to
track. So the pass histogram carries its own set, spanning the target and the
reality either side of it.

The funnel counter is labelled by outcome rather than split into two metrics.
§14 asks for the candidate→published *ratio*, and a ratio wants one series
divided by another with the same label set.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry

from scanner.infrastructure.observability.metrics import counter, histogram

# 1 s to 5 min. §14's targets (2 s per symbol-TF, 30 s and 60 s for the full
# cycle) each sit on a boundary so a quantile lands where the doctrine reads,
# and the tail runs past the 104 s measured on the VM so a regression beyond
# it is still visible rather than saturating.
PASS_BUCKETS: tuple[float, ...] = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


class PrometheusDetectionMetrics:
    def __init__(self, *, registry: CollectorRegistry = REGISTRY) -> None:
        self._pass_seconds = histogram(
            "scanner_detection_pass_seconds",
            "Wall time of one detection pass over a symbol-timeframe (SLS §14)",
            ("timeframe",),
            buckets=PASS_BUCKETS,
            registry=registry,
        )

        self._publications = counter(
            "scanner_detection_publications_total",
            "Publication decisions by outcome: published, or the §15.3 check "
            "that refused (SLS §12.2's auditable funnel)",
            ("timeframe", "outcome"),
            registry=registry,
        )

    def observe_pass(self, seconds: float, *, symbol: str, timeframe: str) -> None:
        # Deliberately not labelled by symbol. At ~400 symbols by 5 timeframes
        # by 9 buckets that is 18,000 series for one histogram, and §14's
        # targets are stated per timeframe, not per symbol. The symbol is in
        # the structured log for the pass, where a one-off question can find
        # it without paying for it continuously.
        self._pass_seconds.labels(timeframe=timeframe).observe(seconds)

    def record_publication(self, outcome: str, *, timeframe: str) -> None:
        self._publications.labels(timeframe=timeframe, outcome=outcome).inc()
