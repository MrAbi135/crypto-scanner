"""Market data read endpoints (API Spec §18.7).

Implements the `Candles` row only -- the subset Roadmap §7.2 step 3 exists to
serve. `session-stats`, `sentiment` and `incidents` remain `DESIGNED`.

**Auth is not implemented here, and that is a deviation.** The spec marks these
rows 🔑 with `tf:{tf}` entitlements, and identity is S10-S12. Rather than invent
a placeholder auth that would have to be unpicked, the endpoints ship
unauthenticated and the router refuses to mount unless the deployment has
explicitly declared itself private. See `build_read_api`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from scanner.application.ports import CandleRepository, Clock
from scanner.interfaces.api.deps import get_candles, get_clock
from scanner.interfaces.api.envelope import Freshness, success
from scanner.interfaces.api.errors import bad_request
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
