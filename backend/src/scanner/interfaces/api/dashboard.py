"""§18.3's dashboard group: the status strip, and the overview's honest subset.

`/overview` serves the two things the hub can answer from recorded facts --
the ranked board's head, and the platform's latest consumed levels -- and
names what it cannot in `not_measured`, same contract as `/status`. The regime
ribbon needs breadth statistics no engine computes, compression has no
aggregation, and the watchlist pulse needs S17's tables; none of the three is
stubbed with a plausible shape, because a dashboard that renders invented
numbers is worse than one that renders none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from scanner.application.feed import FeedRow as LiveRow
from scanner.application.feed import LiveFeedService
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository, RecentSweepRecord
from scanner.application.ports.repositories import IncidentRecord, IncidentRepository
from scanner.domain.common.coverage import Coverage, candles_behind, coverage_of
from scanner.interfaces.api.deps import (
    get_candles,
    get_clock,
    get_evidence,
    get_feed,
    get_incidents,
)
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


# The board's head, not a different ranking: the same §9.2 order the feed
# serves, cut at the hub's size. A second "top" computed here would eventually
# disagree with the feed about what is on top.
TOP_SIGNALS = 5
RECENT_SWEEPS = 10


@router.get("/overview")
async def overview(
    _: Annotated[CurrentUser, Depends(require_user)],
    feed: Annotated[LiveFeedService, Depends(get_feed)],
    evidence: Annotated[IctEvidenceRepository, Depends(get_evidence)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.3's hub, restricted to what is measurable (Blueprint §21.6).

    Two lists and a set of named absences. `top_signals` is the live board's
    head in the board's own order; `recent_sweeps` is the platform's latest
    consumed levels, which is the one piece of the hub the feed and the chart
    do not already show somewhere.
    """
    now = clock.now()

    board = await feed.read()
    sweeps = await evidence.list_recent_sweeps(limit=RECENT_SWEEPS)

    return success(
        {
            "top_signals": [_top_row(row) for row in board.rows[:TOP_SIGNALS]],
            # The feed's denominator travels with the head for the feed's own
            # reason: five rows out of five and out of ninety are different
            # markets.
            "live_total": board.live_total,
            "recent_sweeps": [_sweep_row(row) for row in sweeps],
            "not_measured": [
                "regime ribbon — needs breadth statistics no engine computes",
                "compression — no aggregation exists",
                "watchlist pulse — needs S17's workspace tables",
            ],
        },
        generated_at=now,
        freshness=Freshness(
            state="RECORDED",
            observed_at=sweeps[0].transitioned_at if sweeps else None,
        ),
    )


def _top_row(row: LiveRow) -> dict[str, Any]:
    signal = row.signal

    return {
        "rank": row.position,
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe.value,
        "direction": signal.direction,
        "archetype": signal.archetype,
        "grade": signal.grade,
        "confidence": str(signal.final_confidence),
        "display_rank": str(row.display),
        "lifecycle_state": row.lifecycle_state,
    }


def _sweep_row(row: RecentSweepRecord) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "timeframe": row.timeframe.value,
        "pool_id": row.pool_id,
        # None when the pool row no longer exists -- the transition outlives
        # the object on purpose, and a vanished side must not be guessed.
        "side": row.side,
        "event": row.to_state,
        "reason": row.reason,
        "at": row.transitioned_at.isoformat(),
    }


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
