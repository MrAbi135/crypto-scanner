"""How fresh the daily depth §6.4 reads is (Bundle D monitoring, owner M2 Q3).

The SLS sets no age limit on the snapshot §6.4 reads: a symbol whose daily
collection stopped keeps scoring against its last snapshot. The owner ruled
(M2 Q3, option a) to keep it that way in doctrine and to add an alarm instead:
when any symbol's newest depth snapshot is more than two days old, Prometheus
alerts. This is monitoring only; nothing here changes a score.

The worker publishes one number, the newest stamp of the *stalest* symbol, as a
unix timestamp. The alert compares it with `time()`, so it fires even when the
worker itself has stopped refreshing it -- a gauge holding "seconds old" would
freeze at its last healthy value and stay quiet exactly when it matters.

A symbol with no snapshot at all is left out: it is newly listed and the daily
job has not reached it yet, which §6.4 already treats as an incomplete check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

STALE_AFTER = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class DepthFreshness:
    oldest_latest_stamp: datetime | None
    stale_symbols: int
    symbols_with_snapshots: int


def depth_freshness(
    latest_stamps: Mapping[str, datetime],
    symbols: Iterable[str],
    now: datetime,
) -> DepthFreshness:
    """The stalest newest-snapshot among `symbols`, and how many are stale."""
    stamps = [latest_stamps[s] for s in symbols if s in latest_stamps]

    return DepthFreshness(
        oldest_latest_stamp=min(stamps) if stamps else None,
        stale_symbols=sum(1 for stamp in stamps if now - stamp > STALE_AFTER),
        symbols_with_snapshots=len(stamps),
    )
