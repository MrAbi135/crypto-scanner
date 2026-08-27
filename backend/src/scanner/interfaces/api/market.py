"""Market data read endpoints (API Spec §18.7).

`Candles` and `Incidents`. `session-stats` and `sentiment` remain `DESIGNED`.

The auth deviation this file used to describe is closed: identity landed in
S10-S12 and `build_read_api` mounts this router behind `require_user` like
every other read group. The `tf:{tf}` entitlement on the candles row is still
outstanding -- authentication is not entitlement -- and that is the remaining
gap, not authentication itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.repositories import IncidentRecord, IncidentRepository
from scanner.interfaces.api.deps import get_candles, get_clock, get_incidents
from scanner.interfaces.api.envelope import Freshness, success
from scanner.interfaces.api.errors import bad_request
from scanner.interfaces.api.query import QueryRejectedError
from scanner.interfaces.api.window import window_end
from scanner.shared import Timeframe

router = APIRouter(prefix="/api/v1/market", tags=["market"])

# §8: default 50, max 200 unless the endpoint documents an override. Charts
# need a screen of candles, so this row documents 1000.
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000


@router.get("/candles")
async def get_candles_endpoint(
    request: Request,
    symbol_id: Annotated[str, Query(min_length=1, max_length=32)],
    timeframe: Annotated[str, Query()],
    candles: Annotated[CandleRepository, Depends(get_candles)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    to: Annotated[datetime | None, Query()] = None,
) -> dict[str, object]:
    """OHLCV window for charts. Closed candles only (§18.7).

    The forming candle is deliberately absent: it is not a fact yet, and every
    detector in the system refuses to see it. An endpoint that returned it
    would put the chart and the doctrine one bar out of agreement, which is
    precisely the confusion S13a exists to remove.
    """
    parsed = _timeframe(request, timeframe)

    symbol = symbol_id.upper()

    end = to or await window_end(candles, symbol, parsed, clock)
    start = end - parsed.duration * limit

    series = await candles.fetch_series(symbol, parsed, start, end)

    rows = [
        {
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "quote_volume": candle.quote_volume,
            "taker_buy_volume": candle.taker_buy_volume,
            "trade_count": candle.trade_count,
            "source": candle.source.value,
        }
        for candle in series
    ]

    return success(
        rows,
        generated_at=clock.now(),
        # A window read from the stored record is exactly as fresh as the
        # record. Claiming more would be the §45.3 violation; claiming a
        # per-source state we have not measured would be worse.
        freshness=Freshness(
            state="RECORDED",
            observed_at=series[-1].close_time if series else None,
        ),
        page={
            "count": len(rows),
            "has_more": len(rows) == limit,
        },
    )


def _timeframe(request: Request, raw: str) -> Timeframe:
    try:
        return Timeframe.parse(raw)
    except Exception:
        raise bad_request(
            request,
            "timeframe is not a scanned timeframe",
            field="timeframe",
        ) from None


# §18.7 documents `symbol_id` and `open_only`, and nothing else. §9 refuses a
# filter the server did not apply, so anything further is a 422 rather than a
# ledger quietly returning everything and calling itself filtered.
INCIDENT_LIMIT = 100


@router.get("/incidents")
async def list_incidents(
    request: Request,
    incidents: Annotated[IncidentRepository, Depends(get_incidents)],
    clock: Annotated[Clock, Depends(get_clock)],
    symbol_id: Annotated[str | None, Query(max_length=32)] = None,
    open_only: Annotated[bool, Query()] = False,
) -> dict[str, object]:
    """DDD T8's data-honesty ledger.

    §18.7 calls this row "public honesty -- not admin-gated", and the
    permissions column says `user`. That is the whole point of it: a reader who
    can see a signal can see what was wrong with the data underneath it. Hiding
    the ledger behind an operator role would leave the signal looking better
    than the data it was computed from, which is the failure §2.12 and §15.3
    exist to prevent.

    Resolved incidents are included by default. An incident that was found and
    fixed is the part of the ledger that shows the honesty working, and a
    default of open-only would make a well-run week look like an empty one.
    """
    # Refused the way every other §9 row refuses -- a `QueryRejectedError`,
    # which the app maps to a field-precise 422. A 400 here would make this the
    # one endpoint that answers a bad filter differently from the rest.
    unknown = sorted(set(request.query_params) - {"symbol_id", "open_only"})

    if unknown:
        raise QueryRejectedError(
            f"unknown query parameter: {unknown[0]}",
            field=unknown[0],
        )

    rows = await incidents.list_ledger(
        symbol=symbol_id,
        open_only=open_only,
        limit=INCIDENT_LIMIT,
    )

    return success(
        [_incident(row) for row in rows],
        generated_at=clock.now(),
        # The ledger is a record of things that happened, not a live reading.
        freshness=Freshness(state="RECORDED", observed_at=rows[0].started_at if rows else None),
        page={"count": len(rows), "has_more": len(rows) == INCIDENT_LIMIT},
    )


def _incident(row: IncidentRecord) -> dict[str, object]:
    return {
        "id": row.id,
        "scope": row.scope_type,
        "type": row.incident_type,
        "symbol": row.symbol,
        "timeframe": row.timeframe.value if row.timeframe is not None else None,
        "candle_span": row.candle_span,
        "started_at": row.started_at.isoformat(),
        # Both, always. `resolved_at` alone would leave a reader guessing what
        # was done, and `resolution` alone would not say when it stopped.
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at is not None else None,
        "resolution": row.resolution,
        "open": row.resolved_at is None,
        "notes": row.notes,
    }
