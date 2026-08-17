"""Coin doctrine read endpoints (API Spec §18.5).

The three rows S13a draws with: `structure`, `zones`, `liquidity`. `Coin
summary` and `Coin signals` stay `DESIGNED` -- the first needs metadata the
platform does not collect yet, the second needs T17 signals from S9.

All three are doctrine-derived, so every response carries `meta.versions`
(SLS §15.2). See `envelope.NO_PARAM_SET` for what the param-set half honestly
reports before S8 exists.

Auth is absent here for the reason given in `app.py`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request

from scanner.application.detection.liquidity_replay import LIQUIDITY_ALGO_VERSION
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
)
from scanner.application.ports import Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository
from scanner.interfaces.api.deps import (
    get_clock,
    get_evidence,
    get_pools,
    get_zones,
)
from scanner.interfaces.api.envelope import Freshness, Versions, success
from scanner.interfaces.api.errors import bad_request
from scanner.shared import Timeframe

router = APIRouter(prefix="/api/v1/coins", tags=["coins"])

DEFAULT_WINDOW = 500
MAX_WINDOW = 1000

SymbolId = Annotated[str, Path(min_length=1, max_length=32)]
TimeframeParam = Annotated[str, Query()]


@router.get("/{symbol_id}/structure")
async def get_structure(
    request: Request,
    symbol_id: SymbolId,
    timeframe: TimeframeParam,
    evidence: Annotated[IctEvidenceRepository, Depends(get_evidence)],
    clock: Annotated[Clock, Depends(get_clock)],
    window: Annotated[int, Query(ge=1, le=MAX_WINDOW)] = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Structure events for the chart overlay (§18.5).

    Returns the recorded events rather than recomputing them. The chart must
    show what the engine decided, not a second opinion computed at read time --
    if the two ever disagree, the disagreement is the finding, and it cannot
    surface if the endpoint recomputes.
    """
    parsed = _timeframe(request, timeframe)

    start, end = _window(clock, parsed, window)

    events = await evidence.list_structure(symbol_id.upper(), parsed, start, end)

    rows = [
        {
            "event_type": record.event_type,
            "event_at": record.event_at,
            "algo_version": record.algo_version,
            "evidence": _decode(record.payload),
        }
        for record in events
    ]

    return success(
        rows,
        generated_at=clock.now(),
        freshness=Freshness(
            state="RECORDED",
            observed_at=events[-1].event_at if events else None,
        ),
        versions=Versions(algo_version=STRUCTURE_SHIFT_ALGO_VERSION),
        page={"count": len(rows), "has_more": False},
    )


@router.get("/{symbol_id}/zones")
async def get_zones_endpoint(
    request: Request,
    symbol_id: SymbolId,
    timeframe: TimeframeParam,
    zones: Annotated[IctZoneRepository, Depends(get_zones)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """Live zones for the chart overlay (§18.5).

    The spec's `state?` filter defaults to live, and live is all this row
    serves: `IctZoneRepository` exposes `list_live` only. Historical zone states
    need a repository method that does not exist, so rather than accept a
    `state` parameter and ignore anything but the default -- which would lie by
    accepting it -- the parameter is absent until it can be honoured.
    """
    parsed = _timeframe(request, timeframe)

    live = await zones.list_live(symbol_id.upper(), parsed)

    rows = [
        {
            "zone_id": zone.zone_id,
            "zone_type": zone.zone_type,
            "polarity": zone.polarity,
            "state": zone.state,
            "grade": zone.grade,
            "band_low": zone.band_low,
            "band_high": zone.band_high,
            "refined_low": zone.refined_low,
            "refined_high": zone.refined_high,
            "created_index": zone.created_index,
            "confirmed_index": zone.confirmed_index,
            "parent_zone_id": zone.parent_zone_id,
            "stale_context": zone.stale_context,
            "gap_adjacent": zone.gap_adjacent,
            "updated_at": zone.updated_at,
            "evidence": _decode(zone.evidence),
        }
        for zone in live
    ]

    return success(
        rows,
        generated_at=clock.now(),
        freshness=Freshness(state="RECORDED"),
        versions=Versions(algo_version=STRUCTURE_SHIFT_ALGO_VERSION),
        page={"count": len(rows), "has_more": False},
    )


@router.get("/{symbol_id}/liquidity")
async def get_liquidity(
    request: Request,
    symbol_id: SymbolId,
    timeframe: TimeframeParam,
    pools: Annotated[LiquidityPoolRepository, Depends(get_pools)],
    evidence: Annotated[IctEvidenceRepository, Depends(get_evidence)],
    clock: Annotated[Clock, Depends(get_clock)],
    window: Annotated[int, Query(ge=1, le=MAX_WINDOW)] = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Active pools and recent sweeps (§18.5, SLS §4.5).

    `strength` ships with its component breakdown per SLS §15.4 -- there is no
    representation of a bare score anywhere in this API. The components live in
    the pool's recorded evidence, so they are read back rather than recomputed.
    """
    parsed = _timeframe(request, timeframe)

    symbol = symbol_id.upper()

    start, end = _window(clock, parsed, window)

    active = await pools.list_active(symbol, parsed)
    transitions = await evidence.list_liquidity(symbol, parsed, start, end)

    pool_rows = [
        {
            "pool_id": pool.pool_id,
            "side": pool.side,
            "liquidity_class": pool.liquidity_class,
            "source": pool.source,
            "price": pool.price,
            "band_low": pool.band_low,
            "band_high": pool.band_high,
            "state": pool.state,
            "member_count": pool.member_count,
            "created_index": pool.created_index,
            "strength": {
                "score": pool.strength,
                "components": _decode(pool.evidence).get("strength_components"),
            },
        }
        for pool in active
    ]

    sweeps = [
        {
            "pool_id": record.pool_id,
            "from_state": record.from_state,
            "to_state": record.to_state,
            "reason": record.reason,
            "transitioned_at": record.transitioned_at,
            "candle_index": record.candle_index,
            "evidence": _decode(record.evidence),
        }
        for record in transitions
        if record.reason == "liquidity_sweep"
    ]

    return success(
        {"pools": pool_rows, "sweeps": sweeps},
        generated_at=clock.now(),
        freshness=Freshness(state="RECORDED"),
        versions=Versions(algo_version=LIQUIDITY_ALGO_VERSION),
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


def _window(
    clock: Clock,
    timeframe: Timeframe,
    candles: int,
) -> tuple[datetime, datetime]:
    end = clock.now()

    return end - timeframe.duration * candles, end


def _decode(raw: str) -> Any:
    """Recorded evidence is stored as JSON text; the API returns it as JSON.

    Re-serialising the string verbatim would hand clients an escaped blob to
    parse themselves, and SLS §15.2 makes the evidence chain part of the
    contract rather than an opaque field.
    """
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Not raised: a single corrupt row must not take down a whole chart
        # request, and the caller can see that this object's evidence is
        # unreadable while every other object still renders.
        return {"unreadable": True}
