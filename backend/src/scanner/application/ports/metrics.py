"""What the engine reports about itself, as a port.

SLS §14 sets binding maxima "measured continuously, regressions block release"
and asks for the candidate→published funnel as "a monitored ratio ... alert on
±50% day-over-day shift (doctrine drift detector)". Neither was measured. The
metrics foundation existed, `/internal/metrics` served the default registry,
and the registry was empty of anything this system produces — so Prometheus
has been scraping four processes and learning only that they are up.

A port because the application layer cannot import infrastructure (TAD §27),
and because "how long the pass took" is a fact the engine knows and a
Prometheus client is one way to write it down.

`NullMetrics` is the default everywhere. A metrics call that fails must never
take a detection pass with it, and a constructor that demands a collector
would make every test and every replay script carry one.
"""

from __future__ import annotations

from typing import Protocol


class DetectionMetrics(Protocol):
    def observe_pass(self, seconds: float, *, symbol: str, timeframe: str) -> None:
        """One completed detection pass over one symbol-timeframe.

        SLS §14: "Candle close → all detectors evaluated (per symbol-TF) ≤ 2 s"
        and "Full-universe scan cycle ≤ 30 s; ≤ 60 s p99".
        """
        ...

    def record_publication(self, outcome: str, *, timeframe: str) -> None:
        """One decision on the §12.2 funnel: `published` or a suppression reason.

        Counted per reason rather than as a pass/fail pair, because the ratio
        moving is the signal and *which* check moved it is the diagnosis.
        """
        ...


class NullMetrics:
    """The default: measure nothing, cost nothing, never raise."""

    def observe_pass(self, seconds: float, *, symbol: str, timeframe: str) -> None:
        return None

    def record_publication(self, outcome: str, *, timeframe: str) -> None:
        return None
