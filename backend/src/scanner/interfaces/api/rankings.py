"""§18.6's ranking group.

Two rows: the deterministic board (§9.2) and the weights that produced it
(§9.1). The second is described in the spec as a "doctrine transparency
endpoint", which is the whole reason it exists — a confidence number a reader
cannot interrogate is a number they have to take on trust, and §15.4 says
confidence "is displayed with its factor breakdown — never as a bare number".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from scanner.application.parameters import PARAM_SET_VERSION
from scanner.application.ports import Clock
from scanner.application.ranking import RankingSnapshotService
from scanner.domain.confluence.weights import (
    FACTOR_JUSTIFICATION,
    GRADE_A_FLOOR,
    GRADE_B_FLOOR,
    GRADE_S_FLOOR,
    WEIGHTS,
)
from scanner.interfaces.api.deps import get_clock, get_rankings
from scanner.interfaces.api.envelope import Freshness, success
from scanner.interfaces.api.query import (
    FilterOp,
    QueryRejectedError,
    SortKey,
    parse_filters,
    parse_limit,
    parse_sort,
)
from scanner.interfaces.api.security import CurrentUser, require_user
from scanner.shared import Timeframe

router = APIRouter(prefix="/api/v1/rankings", tags=["rankings"])

# §18.6: "Filters: grade, archetype, tf". SLS vocabulary verbatim, as §9
# requires.
RANKING_FILTERS: dict[str, frozenset[FilterOp]] = {}

# §9.2 is a total order over five stated keys and §10 says rank-ordered
# resources "fix their sort ... client sort parameters are rejected there
# rather than silently overridden". A client sort here would not reorder a
# page, it would reorder a *ranking* — and the position numbers printed beside
# the rows would become wrong.
RANKING_SORT = (SortKey("rank"),)


@router.get("/weights")
async def ranking_weights(
    _: Annotated[CurrentUser, Depends(require_user)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.6's "user-visible §9.1 table" (PRD FC-4.1).

    The justifications are §9.1's own prose, transcribed rather than
    paraphrased. A summary here would let the published reason drift from the
    rule it defends, and a reader has no way to tell the difference — which
    defeats the point of publishing it.

    `param_set_version` travels with them because §9.1 makes the weights
    `P.rank.weights`, versioned, and a table without its version cannot be
    matched to the signals it scored.
    """
    return success(
        {
            "param_set_version": PARAM_SET_VERSION,
            "factors": [
                {
                    "factor": factor.value,
                    "name": factor.name.replace("_", " ").title(),
                    # As a string, like every other number this API returns:
                    # 0.15 is a decimal the client must not re-round.
                    "weight": str(weight),
                    "weight_pct": str(weight * 100),
                    "justification": FACTOR_JUSTIFICATION[factor],
                }
                for factor, weight in WEIGHTS.items()
            ],
            # §9.4, included because a board without its bands is a column of
            # letters. The floors are what "grade B" means.
            "grades": [
                {"grade": "S", "min_confidence": str(GRADE_S_FLOOR)},
                {"grade": "A", "min_confidence": str(GRADE_A_FLOOR)},
                {"grade": "B", "min_confidence": str(GRADE_B_FLOOR)},
            ],
            # §9.4: below the lowest floor is not a weak grade, it is not
            # published. Said here so a client does not invent a "C".
            "below_lowest_floor": "not published",
        },
        generated_at=clock.now(),
        # Doctrine, not market data. It is as fresh as the deployed build and
        # saying anything about feeds here would be borrowing a word that
        # means something else.
        freshness=Freshness(state="STATIC", observed_at=None),
    )


@router.get("")
async def current_rankings(
    request: Request,
    _: Annotated[CurrentUser, Depends(require_user)],
    rankings: Annotated[RankingSnapshotService, Depends(get_rankings)],
    clock: Annotated[Clock, Depends(get_clock)],
    symbols: Annotated[str, Query()],
    timeframe: Annotated[str, Query()],
    at: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """§18.6's deterministic board.

    **The board reports its own denominator.** §8.6 keeps below-floor
    candidates "for calibration", and a board showing only its rows makes a
    quiet market and a broken pipeline look identical — which is exactly the
    confusion that cost days on the staging host, where 64 candidates scored
    and none published.
    """
    params = dict(request.query_params)

    # Parsed for the refusals, not for a value: no filter is applied yet, so
    # accepting one would be §9's "filter the server didn't apply".
    parse_filters(params, allowed=RANKING_FILTERS)
    parse_limit(params.get("limit"))
    parse_sort(params.get("sort"), allowed=frozenset(), default=RANKING_SORT, fixed=True)

    series = Timeframe.parse(timeframe)
    moment = _at(at, series, clock.now())

    snapshot = await rankings.snapshot(
        tuple(s.strip() for s in symbols.split(",") if s.strip()),
        series,
        moment,
    )

    rows = [
        {
            "rank": row.position,
            "symbol": row.setup.symbol,
            "timeframe": row.setup.timeframe.value,
            "direction": row.setup.direction,
            "archetype": row.setup.archetype.value,
            "tier": row.setup.tier.value,
            "confidence": str(row.setup.confidence),
            # §9.3: the *display* rank decays, the recorded confidence does
            # not. Both are returned so a reader can see the difference rather
            # than wonder which number they are looking at.
            "display_rank": str(row.display),
        }
        for row in snapshot.rows
    ]

    return success(
        rows,
        generated_at=clock.now(),
        freshness=Freshness(state="RECORDED", observed_at=snapshot.at),
        page={
            "count": len(rows),
            "has_more": False,
            # The denominator. Not decoration: it is what separates "nothing
            # qualified" from "nothing was evaluated".
            "gate_passers": snapshot.gate_passers,
            "below_floor": snapshot.below_floor,
        },
    )


def _at(raw: str | None, timeframe: Timeframe, now: datetime) -> datetime:
    """The close being ranked, defaulting to the most recent one.

    Floored to the timeframe rather than taken as `now`: §9.2 ranks the
    candidates recorded *at a close*, and asking for a moment between closes
    would return an empty board that looks like a quiet market.
    """
    if raw is not None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise QueryRejectedError(f"at must be an ISO timestamp: {raw}", field="at") from None

        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=now.tzinfo)

    step = int(timeframe.duration.total_seconds())

    return datetime.fromtimestamp(
        (int(now.timestamp()) // step) * step,
        tz=now.tzinfo,
    )
