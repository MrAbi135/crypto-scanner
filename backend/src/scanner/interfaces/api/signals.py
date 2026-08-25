"""§18.8's per-signal rows: detail, evidence chain, lifecycle.

The three rows that make one published signal readable. §18.8's collection rows
— `history` and `statistics` — are the next piece: they need filtered keyset
queries over T17 joined to T19 and a versioned aggregate, which is a different
kind of work from reading one sealed row back.

**Everything here comes out of the stored payload, never from a recomputation.**
§12.1: "evidence, zones, levels never mutate post-creation". A detail endpoint
that rebuilt the evidence from current market state would show a signal that
drifts every time it is opened, and the hash beside it would stop matching what
it describes. So the payload column is parsed and served; the extracted columns
are used only where the payload does not carry something (the lifecycle state
lives in T18, by construction).

**§15.4's honesty rules apply to what this returns.** "Expired signals display
as expired, failed as failed." That is the lifecycle row's whole job, and it is
why the detail row carries the state rather than leaving a client to infer
"live" from the absence of an outcome.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from scanner.application.ports import Clock
from scanner.application.ports.signal_outcomes import SignalOutcomeRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRecord, SignalRepository
from scanner.application.signal_audit import reseal
from scanner.interfaces.api.deps import (
    get_clock,
    get_outcomes,
    get_signal_transitions,
    get_signals,
)
from scanner.interfaces.api.envelope import Freshness, Versions, success
from scanner.interfaces.api.errors import not_found
from scanner.interfaces.api.security import CurrentUser, require_user

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])

Projection = Literal["summary", "full"]


class Outcome(BaseModel):
    outcome: str
    resolved_at: datetime
    elapsed_candles: int
    mfe_r: Decimal
    mae_r: Decimal


class Transition(BaseModel):
    from_state: str
    to_state: str
    at_candle_open_time: datetime
    recorded_at: datetime
    # §18.8 names this row as including them, and §12.3 records a wick through
    # the invalidation as a fact about the candle whether or not the signal
    # moved. A history that dropped them would show a quiet life for a signal
    # that was tested three times.
    stress_test: bool
    refresh: bool
    evidence: dict[str, Any]


def _summary(signal: SignalRecord, state: str | None) -> dict[str, Any]:
    """The row a list or a card renders, without the sealed payload.

    `lifecycle_state` is None only when T18 has no row for a published signal,
    which §12.2 makes impossible — the PUBLISHED transition is written with the
    signal. Passed through as null rather than defaulted to "PUBLISHED": a
    default would turn a broken write into a signal that looks fine.
    """
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe.value,
        "direction": signal.direction,
        "archetype": signal.archetype,
        "grade": signal.grade,
        "confidence": str(signal.final_confidence),
        "entry": {
            "proximal": str(signal.entry_proximal),
            "distal": str(signal.entry_distal),
        },
        "invalidation": str(signal.invalidation_level),
        "targets": json.loads(signal.target_bands),
        "published_at": signal.published_at.isoformat(),
        "ttl_candles": signal.ttl_candles,
        "lifecycle_state": state,
        "versions": {
            "algo_version": signal.algo_version,
            "param_set_version": signal.param_set_version,
        },
    }


@router.get("/{signal_id}")
async def signal_detail(
    request: Request,
    signal_id: str,
    _: Annotated[CurrentUser, Depends(require_user)],
    signals: Annotated[SignalRepository, Depends(get_signals)],
    transitions: Annotated[SignalTransitionRepository, Depends(get_signal_transitions)],
    outcomes: Annotated[SignalOutcomeRepository, Depends(get_outcomes)],
    clock: Annotated[Clock, Depends(get_clock)],
    projection: Annotated[Projection, Query()] = "summary",
) -> dict[str, Any]:
    """§18.8: "`full` includes sealed payload fields; hash included"."""

    signal = await signals.get(signal_id)

    if signal is None:
        raise not_found(request, "No such signal.")

    state = await transitions.current_state(signal_id)

    data = _summary(signal, state)

    resolved = await outcomes.get(signal_id)

    if resolved is not None:
        # §15.4: "Expired signals display as expired, failed as failed — the
        # platform's record is its integrity."
        data["outcome"] = Outcome(
            outcome=resolved.outcome,
            resolved_at=resolved.resolved_at,
            elapsed_candles=resolved.elapsed_candles,
            mfe_r=resolved.mfe_r,
            mae_r=resolved.mae_r,
        ).model_dump(mode="json")

    if projection == "full":
        data["payload"] = json.loads(signal.payload)
        data["payload_hash"] = signal.payload_hash
        # Recomputed here rather than trusted. §15.3(5) puts the hash on the
        # signal "for audit", and an audit value nobody ever checks is a
        # column. A client can verify it itself; this saves it having to
        # reproduce our canonical JSON to do so.
        data["payload_hash_verified"] = reseal(signal.payload) == signal.payload_hash

    return success(
        data,
        generated_at=clock.now(),
        # A published signal is a snapshot, not a live reading: it was sealed
        # at `published_at` and §12.1 forbids it changing. `observed_at` is
        # that moment rather than now, so a client cannot render a
        # three-day-old signal as freshly observed.
        freshness=Freshness(state="RECORDED", observed_at=signal.published_at),
        versions=Versions(
            algo_version=signal.algo_version,
            param_set_version=signal.param_set_version,
        ),
    )


@router.get("/{signal_id}/evidence")
async def signal_evidence(
    request: Request,
    signal_id: str,
    _: Annotated[CurrentUser, Depends(require_user)],
    signals: Annotated[SignalRepository, Depends(get_signals)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.8's evidence chain, "each with event refs + candle natural keys".

    Read straight out of the sealed payload. §12.1 froze it at publication, and
    the whole point of the row is that a chart can deep-link to the candles a
    claim was made from — which requires the ids that were true then, not the
    ones a fresh detection pass would produce now.

    The natural keys travel beside the ids because §15.2's evidence is an
    "event-id chain": an id alone deep-links to nothing without knowing which
    symbol, timeframe and candle it belongs to.
    """
    signal = await signals.get(signal_id)

    if signal is None:
        raise not_found(request, "No such signal.")

    payload = json.loads(signal.payload)

    return success(
        {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe.value,
            "evidence": payload.get("evidence", {}),
            "confidence": payload.get("confidence", {}),
        },
        generated_at=clock.now(),
        freshness=Freshness(state="RECORDED", observed_at=signal.published_at),
        versions=Versions(
            algo_version=signal.algo_version,
            param_set_version=signal.param_set_version,
        ),
    )


@router.get("/{signal_id}/transitions")
async def signal_transitions(
    request: Request,
    signal_id: str,
    _: Annotated[CurrentUser, Depends(require_user)],
    signals: Annotated[SignalRepository, Depends(get_signals)],
    transitions: Annotated[SignalTransitionRepository, Depends(get_signal_transitions)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.8's lifecycle row: SLS §12's state history, "incl. stress_test events".

    The existence check runs against T17 first. Returning an empty list for an
    unknown id would be indistinguishable from a real signal whose history was
    lost, and §18.8 gives this row a `NOT_FOUND`.
    """
    if await signals.get(signal_id) is None:
        raise not_found(request, "No such signal.")

    rows = await transitions.list_for_signal(signal_id)

    return success(
        [
            Transition(
                from_state=row.from_state,
                to_state=row.to_state,
                at_candle_open_time=row.at_candle_open_time,
                recorded_at=row.recorded_at,
                stress_test=row.stress_test,
                refresh=row.refresh,
                evidence=_evidence(row.trigger_evidence),
            ).model_dump(mode="json")
            for row in rows
        ],
        generated_at=clock.now(),
        # The last thing that happened to the signal, not the moment this was
        # asked. A lifecycle view whose freshness said "now" would imply the
        # signal had been observed now.
        freshness=Freshness(
            state="RECORDED",
            observed_at=rows[-1].at_candle_open_time if rows else None,
        ),
        page={"count": len(rows), "has_more": False},
    )


def _evidence(raw: str) -> dict[str, Any]:
    """The stored evidence, or a marker that it could not be read.

    An unparseable blob is returned as one rather than raised on: the rest of
    the history is still true, and a 500 on one malformed row would hide the
    nine good ones. The marker keeps it from reading as an empty observation.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"unparseable": raw[:200]}

    return parsed if isinstance(parsed, dict) else {"value": parsed}
