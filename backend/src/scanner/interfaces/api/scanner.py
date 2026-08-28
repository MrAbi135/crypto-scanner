"""§18.4's scanner group.

`/scanner/feed` first, because the spec calls it "THE core read" (PRD FC-2.2)
and because it is the only row in §18.4 that has anything to serve today: the
universe rows read a registry that nothing populates yet, which is a defect of
its own and not one to be hidden behind an endpoint that returns an empty list
and looks fine doing it.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from scanner.application.feed import FeedRow, LiveFeedService
from scanner.application.ports import Clock, SymbolRepository, UniverseRow
from scanner.interfaces.api.deps import get_clock, get_feed, get_symbols
from scanner.interfaces.api.envelope import Freshness, success
from scanner.interfaces.api.query import (
    Filter,
    FilterOp,
    SortKey,
    parse_filters,
    parse_limit,
    parse_sort,
)
from scanner.interfaces.api.security import CurrentUser, require_user

router = APIRouter(prefix="/api/v1/scanner", tags=["scanner"])

# §18.4: "Filters §9 (archetype, grade, tf, direction, tier, category,
# htf_alignment, watchlist_id)". Only the four the published signal actually
# carries are accepted here. The other four are not silently ignored -- §9 is
# explicit that a filter the server did not apply must be refused -- so they
# arrive as a 422 until the row can honour them, which is a smaller lie than
# returning an unfiltered board.
FEED_FILTERS: dict[str, frozenset[FilterOp]] = {
    "archetype": frozenset({FilterOp.EQ, FilterOp.IN}),
    "grade": frozenset({FilterOp.EQ, FilterOp.IN}),
    "timeframe": frozenset({FilterOp.EQ, FilterOp.IN}),
    "direction": frozenset({FilterOp.EQ, FilterOp.IN}),
}

# §18.4 says "no sort (fixed §10)". §9.2 is a total order and a client sort
# would not reorder a page, it would reorder a *ranking* -- the position
# numbers printed beside the rows would become wrong.
FEED_SORT = (SortKey("rank"),)

# §18.4 documents no sort on the universe row either; tier then symbol is the
# order, and it is the server's.
UNIVERSE_SORT = (SortKey("tier"),)


@router.get("/feed")
async def live_feed(
    request: Request,
    _: Annotated[CurrentUser, Depends(require_user)],
    feed: Annotated[LiveFeedService, Depends(get_feed)],
) -> dict[str, Any]:
    """Every live signal, ordered by §9.2 and decayed by §9.3.

    Not scoped to a close, unlike §18.6's rankings: that board answers what a
    timeframe offered at a moment, and this one answers what is on the table
    now, across every symbol and timeframe at once.

    **Both numbers travel.** §9.3's decay moves `display_rank`; the recorded
    `confidence` does not move at all. §15.4 wants the breakdown visible rather
    than "a bare number", and a reader given only the decayed figure cannot
    tell a weakening signal from a weak one.
    """
    params = dict(request.query_params)

    filters = parse_filters(params, allowed=FEED_FILTERS)
    parse_limit(params.get("limit"))
    parse_sort(params.get("sort"), allowed=frozenset(), default=FEED_SORT, fixed=True)

    board = await feed.read()

    rows = [row for row in board.rows if _matches(row, filters)]

    return success(
        [_row(row) for row in rows],
        generated_at=board.generated_at,
        # RECORDED, not a new word. §2.12's vocabulary is Fresh / Stale / Dead
        # and it describes *feeds*; these rows are signals written at a close,
        # exactly like §18.6's board, and inventing "LIVE" would put a state in
        # the envelope that the doctrine does not define.
        #
        # `observed_at` is the newest publication on the board rather than now:
        # the question a reader is asking is how old the freshest thing here
        # is, and `generated_at` would answer it with the time they asked.
        freshness=Freshness(
            state="RECORDED",
            observed_at=max((row.signal.published_at for row in rows), default=None),
        ),
        page={
            "count": len(rows),
            "has_more": False,
            # The denominator, before filtering. Without it a filter that
            # matched nothing and a market that offered nothing render the
            # same, which is the confusion §18.6's board already carries a
            # count to avoid.
            "live_total": board.live_total,
        },
    )


def _row(row: FeedRow) -> dict[str, Any]:
    signal = row.signal

    return {
        "rank": row.position,
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe.value,
        "direction": signal.direction,
        "archetype": signal.archetype,
        "grade": signal.grade,
        # As strings, like every other number this API returns: these are
        # decimals a client must not re-round.
        "confidence": str(signal.final_confidence),
        "display_rank": str(row.display),
        "age_candles": row.elapsed_candles,
        "entry": {
            "proximal": str(signal.entry_proximal),
            "distal": str(signal.entry_distal),
        },
        "invalidation": str(signal.invalidation_level),
        "targets": json.loads(signal.target_bands),
        "published_at": signal.published_at.isoformat(),
        "ttl_candles": signal.ttl_candles,
        "lifecycle_state": row.lifecycle_state,
        "versions": {
            "algo_version": signal.algo_version,
            "param_set_version": signal.param_set_version,
        },
    }


def _matches(row: FeedRow, filters: tuple[Filter, ...]) -> bool:
    """Apply §9's parsed filters to one row.

    In the interface rather than the service because the service answers "what
    is live", which is a question about the world; filtering is a question
    about this request. Keeping them apart is also what lets `live_total`
    report the unfiltered count honestly.
    """
    field_of = {
        "archetype": row.signal.archetype,
        "grade": row.signal.grade,
        "timeframe": row.signal.timeframe.value,
        "direction": row.signal.direction,
    }

    # `values` is a tuple even for EQ, so both operators read the same field
    # and neither has to know what the other holds.
    return all(field_of[parsed.field] in parsed.values for parsed in filters)


# §18.4 documents `tier`, `category` and `status`. `category` is not a column
# the registry has -- §1.4 tiers by liquidity and nothing classifies a symbol
# by sector -- so offering it would be a filter the server cannot apply, which
# §9 calls "a lie the client believes". It is absent rather than accepted and
# ignored.
UNIVERSE_FILTERS: dict[str, frozenset[FilterOp]] = {
    "tier": frozenset({FilterOp.EQ}),
    "status": frozenset({FilterOp.EQ}),
}

UNIVERSE_LIMIT = 200

# §1.4: seven daily observations before any evaluation runs, then seven
# consecutive passing evaluations to promote. Published so a reader can see
# what the counters below are counting towards.
REQUIRED_OBSERVATION_DAYS = 7
REQUIRED_PROMOTION_DAYS = 7


@router.get("/universe")
async def universe(
    request: Request,
    _: Annotated[CurrentUser, Depends(require_user)],
    symbols: Annotated[SymbolRepository, Depends(get_symbols)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.4's universe view (DDD T1/T2).

    **The observation count is the point of this row.** Every symbol on a young
    host reads `INELIGIBLE / QUARANTINE` with `consecutive_passes = 0`, which
    is indistinguishable from a universe layer that has stopped -- and was
    misread as exactly that. The count says which: seven observations are
    needed before an evaluation runs at all, so a symbol at four has not failed
    anything, it has not been assessed.

    §18.4 also names "warmup state" and "TFs scanned". Neither is served, and
    neither is faked: §1.9's warm-up is a question per symbol *and timeframe*
    that the coin rows answer, and the scanned timeframe set is engine
    configuration rather than a property of a symbol. A column invented here
    would be a number this endpoint has not been told.
    """
    params = dict(request.query_params)

    filters = parse_filters(params, allowed=UNIVERSE_FILTERS)
    parse_limit(params.get("limit"))
    parse_sort(params.get("sort"), allowed=frozenset(), default=UNIVERSE_SORT, fixed=True)

    chosen = {parsed.field: parsed.values[0] for parsed in filters}

    rows = await symbols.list_universe(
        status=chosen.get("status"),
        tier=chosen.get("tier"),
        limit=UNIVERSE_LIMIT,
    )

    observations = await symbols.count_observations()

    return success(
        [_universe_row(row, observations.get(row.exchange_symbol, 0)) for row in rows],
        generated_at=clock.now(),
        # The registry is what the daily job last wrote, not a live reading.
        freshness=Freshness(state="RECORDED", observed_at=None),
        page={
            "count": len(rows),
            "has_more": len(rows) == UNIVERSE_LIMIT,
            "required_observation_days": REQUIRED_OBSERVATION_DAYS,
            "required_promotion_days": REQUIRED_PROMOTION_DAYS,
        },
    )


def _universe_row(row: UniverseRow, observations: int) -> dict[str, Any]:
    return {
        "symbol": row.exchange_symbol,
        "base_asset": row.base_asset,
        "quote_asset": row.quote_asset,
        "status": row.status,
        "tier": row.tier.value,
        "candidate_tier": row.candidate_tier.value if row.candidate_tier is not None else None,
        "consecutive_passes": row.consecutive_passes,
        "consecutive_failures": row.consecutive_failures,
        "observation_days": observations,
        # Said rather than left to be inferred from two counters and a
        # threshold. "Not yet assessed" and "assessed and failing" are the two
        # states this page exists to separate.
        "assessment": (
            "collecting"
            if observations < REQUIRED_OBSERVATION_DAYS
            else "evaluating"
            if row.consecutive_failures == 0
            else "failing"
        ),
        "first_seen_at": row.first_seen_at.isoformat(),
    }
