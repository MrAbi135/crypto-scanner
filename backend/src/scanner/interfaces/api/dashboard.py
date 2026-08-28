"""§18.3's dashboard group.

The status strip only. §18.3's `overview` is an aggregation of rows that mostly
do not exist yet, and `regime` needs breadth statistics no engine computes --
neither is served, and neither is stubbed with a plausible shape, because a
dashboard that renders invented numbers is worse than one that renders none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.repositories import IncidentRecord, IncidentRepository
from scanner.domain.common.coverage import Coverage, candles_behind, coverage_of
from scanner.interfaces.api.deps import get_candles, get_clock, get_incidents
from scanner.interfaces.api.envelope import Freshness, success
from scanner.interfaces.api.security import CurrentUser, require_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/status")
async def status(
    _: Annotated[CurrentUser, Depends(require_user)],
    candles: Annotated[CandleRepository, Depends(get_candles)],
    incidents: Annotated[IncidentRepository, Depends(get_incidents)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """PRD FC-1.2's data honesty surface.

    **What is served, and it is less than §18.3 lists.** The spec names "feed
    freshness set, degraded symbol-TFs, last scan cycle ms, storm-mode flag".
    The first two are answerable from the database. The last two are not:
    the scan duration and the storm flag live in the engine process, which this
    one does not share memory or a cache with, and inventing either would put a
    number on the honesty surface that nothing measured.

    **Coverage is not §2.12's freshness, and does not borrow its words.** That
    table is in seconds of stream lag, measured where the stream arrives. This
    answers what the stored candles can answer -- has the next close happened,
    and if it should have, did it -- using the same classifier the ingest
    readiness probe uses, so the two cannot disagree.
    """
    now = clock.now()

    series = await candles.newest_per_series()

    feeds = [
        {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "coverage": coverage_of(newest, timeframe, now).value,
            "newest_close": (newest + timeframe.duration).isoformat(),
            # Alongside the state, not instead of it: BEHIND by three closes
            # and BEHIND by three hundred are the same state and not the same
            # problem.
            "candles_behind": candles_behind(newest, timeframe, now),
        }
        for symbol, timeframe, newest in series
    ]

    behind = [feed for feed in feeds if feed["coverage"] == Coverage.BEHIND.value]

    open_incidents = list(await incidents.list_open())

    return success(
        {
            "feeds": feeds,
            # The two counts a reader wants before reading the list, and the
            # reason the list is worth reading at all.
            "behind_count": len(behind),
            "degraded": [_degraded(row) for row in open_incidents],
            "degraded_count": len(open_incidents),
            # Named rather than omitted. A status strip that quietly leaves out
            # two of the four things it is supposed to show reads as a strip
            # that checked them and found nothing wrong.
            "not_measured": [
                "last_scan_cycle_ms — lives in the engine process",
                "storm_mode — lives in the engine process",
                "§2.12 stream lag — measured at ingest, in seconds",
            ],
        },
        generated_at=now,
        freshness=Freshness(
            state="RECORDED",
            observed_at=max((newest for _, _, newest in series), default=None),
        ),
    )


def _degraded(row: IncidentRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.incident_type,
        "symbol": row.symbol,
        "timeframe": row.timeframe.value if row.timeframe is not None else None,
        "started_at": _iso(row.started_at),
        "candle_span": row.candle_span,
    }


def _iso(value: datetime) -> str:
    return value.isoformat()
