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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from scanner.application.ports import Clock
from scanner.application.ports.signal_outcomes import SignalOutcomeRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRecord, SignalRepository
from scanner.application.ports.track_record import (
    ArchivedSignal,
    GroupBy,
    HistoryFilters,
    OutcomeCounts,
    TrackRecordRepository,
    TrackRecordStatistics,
)
from scanner.application.signal_audit import reseal
from scanner.domain.lifecycle.track_record import CONFIDENCE_LEVEL, GroupStats
from scanner.interfaces.api.deps import (
    get_clock,
    get_cursors,
    get_outcomes,
    get_signal_transitions,
    get_signals,
    get_track_record,
    get_track_statistics,
)
from scanner.interfaces.api.envelope import Freshness, Versions, success
from scanner.interfaces.api.errors import not_found, semantic_rejection
from scanner.interfaces.api.query import (
    CursorCodec,
    Filter,
    FilterOp,
    QueryRejectedError,
    SortKey,
    parse_filters,
    parse_limit,
    parse_sort,
)
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


# §18.8's history filters, as §9's closed set. Every value the endpoint will
# accept, and nothing else — an unknown one is a 422 rather than a filter
# quietly not applied.
#
# Vocabulary is SLS's verbatim, which §9 requires: `archetype`, `grade`,
# `timeframe`, `direction` are the doctrine's own words, not an API dialect.
HISTORY_FILTERS: dict[str, frozenset[FilterOp]] = {
    "outcome": frozenset({FilterOp.EQ, FilterOp.IN}),
    "archetype": frozenset({FilterOp.EQ, FilterOp.IN}),
    "grade": frozenset({FilterOp.EQ, FilterOp.IN}),
    "timeframe": frozenset({FilterOp.EQ, FilterOp.IN}),
    "symbol_id": frozenset({FilterOp.EQ, FilterOp.IN}),
    "algo_version": frozenset({FilterOp.EQ, FilterOp.IN}),
    "published_at": frozenset({FilterOp.GTE, FilterOp.LTE}),
}

# §8 wants "a documented default sort" and a total order. Newest first, with
# the id breaking ties, is the same key the cursor carries — a page boundary
# between two signals published on one close would otherwise lose one of them.
HISTORY_SORT = (SortKey("published_at", descending=True), SortKey("signal_id", descending=True))


@router.get("/history")
async def signal_history(
    request: Request,
    _: Annotated[CurrentUser, Depends(require_user)],
    archive: Annotated[TrackRecordRepository, Depends(get_track_record)],
    cursors: Annotated[CursorCodec, Depends(get_cursors)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """§18.8's immutable archive (PRD FC-10.1).

    **Declared before `/{signal_id}`** — FastAPI matches in declaration order,
    and the path row would otherwise swallow "history" as a signal id and
    answer 404. The same trap waits for `/statistics`.

    **"Free tier: full access — honesty never paywalled" (§18.8).** There is no
    entitlement check here and there will not be one: the track record is the
    platform's claim about itself, and a paywalled failure rate is a curated
    one.

    Rejected sorts, unknown filters and bad limits all raise
    `QueryRejectedError`, which the app-level handler turns into §7's 422. The
    endpoint does not catch them, because catching would mean deciding again
    what the grammar already decided.
    """
    params = dict(request.query_params)

    limit = parse_limit(params.get("limit"))
    filters = parse_filters(params, allowed=HISTORY_FILTERS)

    # Sortable, but only on the documented key: a second ordering would need a
    # second cursor shape, and §8 requires the walk to be stable under
    # concurrent inserts in whichever order it is walking.
    parse_sort(
        params.get("sort"),
        allowed=frozenset({"published_at"}),
        default=HISTORY_SORT,
    )

    raw_cursor = params.get("cursor")
    after = cursors.decode(raw_cursor, now=clock.now()) if raw_cursor else None

    if raw_cursor and after is None:
        # Expired, tampered or malformed — the codec does not distinguish, and
        # neither does this. Refused rather than silently restarted at page
        # one, which would make a paginating client loop forever without ever
        # reporting an error.
        raise semantic_rejection(request, "cursor is invalid or expired", field="cursor")

    page = await archive.history(_filters(filters), limit=limit, after=after)

    rows = [_archived_row(row) for row in page.rows]

    return success(
        rows,
        generated_at=clock.now(),
        freshness=Freshness(
            state="RECORDED",
            observed_at=page.rows[0].signal.published_at if page.rows else None,
        ),
        page={
            "count": len(rows),
            "has_more": page.next_position is not None,
            "next_cursor": (
                cursors.encode(page.next_position, now=clock.now())
                if page.next_position is not None
                else None
            ),
        },
    )


def _filters(parsed: tuple[Filter, ...]) -> HistoryFilters:
    """§9's parsed grammar into the port's shape.

    `published_at` arrives as `gte`/`lte` and becomes a half-open range: the
    upper bound is exclusive in the repository, so `lte` is read as "before the
    start of", which is what a caller giving a date means.
    """
    values: dict[str, tuple[str, ...]] = {}
    published_from: datetime | None = None
    published_to: datetime | None = None

    for item in parsed:
        if item.field == "published_at":
            parsed_at = _timestamp(item.values[0], field=str(item.op.value))

            if item.op is FilterOp.GTE:
                published_from = parsed_at
            else:
                published_to = parsed_at

            continue

        values[item.field] = values.get(item.field, ()) + item.values

    return HistoryFilters(
        outcomes=values.get("outcome", ()),
        archetypes=values.get("archetype", ()),
        grades=values.get("grade", ()),
        timeframes=values.get("timeframe", ()),
        symbols=values.get("symbol_id", ()),
        algo_versions=values.get("algo_version", ()),
        published_from=published_from,
        published_to=published_to,
    )


def _timestamp(raw: str, *, field: str) -> datetime:
    """An ISO timestamp, or §7's 422.

    Naive input is read as UTC rather than refused: the platform is UTC
    throughout (§0), and rejecting `2026-08-24` would make the common case the
    awkward one. Attached explicitly, though — comparing a naive value against
    a `timestamptz` column is the kind of thing that works until a row lands
    near midnight.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise QueryRejectedError(
            f"published_at must be an ISO timestamp: {raw}",
            field=f"filter[published_at][{field}]",
        ) from None

    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _archived_row(row: ArchivedSignal) -> dict[str, Any]:
    """One archive row: the signal, and what became of it.

    The outcome fields are absent while a signal is live rather than null —
    §18.8 promises "signals[] + outcomes (MFE/MAE R, elapsed)", and a null MFE
    beside a real one invites a client to chart it as zero.
    """
    data = _summary(row.signal, None)

    # The lifecycle state is not read here. It would be one T18 query per row,
    # and for the archive the outcome *is* the state — a resolved signal's
    # `outcome` says more than "SUCCESS" would as a state string, and a live
    # one is live.
    data.pop("lifecycle_state")

    if row.outcome is None:
        return data

    data["outcome"] = {
        "outcome": row.outcome,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "elapsed_candles": row.elapsed_candles,
        "mfe_r": str(row.mfe_r) if row.mfe_r is not None else None,
        "mae_r": str(row.mae_r) if row.mae_r is not None else None,
        # PRD FC-10.1: "Delisting-expired signals excluded from quality stats
        # but present in archive". Carried so a reader of the archive can see
        # which rows the statistics row will not be counting.
        "excluded_from_stats": row.excluded_from_stats,
    }

    return data


# §18.8's `window`, as named spans rather than a free-form duration. A caller
# asking for "90d" and getting 90 days is unambiguous; one asking for "3m"
# means 90 or 92 days depending on which months, and a track record that moves
# with the calendar is a track record nobody can reproduce.
STATISTICS_WINDOWS: dict[str, timedelta | None] = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "365d": timedelta(days=365),
    # The default. Constitution §28.6 makes the whole record the claim, so the
    # unwindowed view is the honest starting point and the spans narrow from
    # it.
    "all": None,
}


@router.get("/statistics")
async def signal_statistics(
    request: Request,
    _: Annotated[CurrentUser, Depends(require_user)],
    stats: Annotated[TrackRecordStatistics, Depends(get_track_statistics)],
    clock: Annotated[Clock, Depends(get_clock)],
    group_by: Annotated[GroupBy, Query()] = GroupBy.ARCHETYPE,
    window: Annotated[str, Query()] = "all",
) -> dict[str, Any]:
    """§18.8's aggregate track record.

    **Declared before `/{signal_id}`**, for the reason `/history` is — see that
    row.

    **Version-segmented always.** §18.8 says so and it is not a formality: a
    hit rate averaged over two algorithm versions is the average of two
    different scanners, and the number describes neither. Every group carries
    its `algo_version` whatever axis was asked for.

    **Delisting-expired signals are excluded here and only here** (PRD
    FC-10.1). They stay in the archive; a signal that expired because its
    symbol was delisted says nothing about target selection, and counting it
    would make a venue decision look like a scanner failure.
    """
    if window not in STATISTICS_WINDOWS:
        raise semantic_rejection(
            request,
            f"unknown window: {window}. One of: {', '.join(STATISTICS_WINDOWS)}",
            field="window",
        )

    now = clock.now()
    span = STATISTICS_WINDOWS[window]

    counts = await stats.outcome_counts(
        group_by=group_by,
        since=now - span if span is not None else None,
    )

    groups = [_group(row, group_by) for row in counts]

    return success(
        groups,
        generated_at=now,
        # The archive is append-only and every row in it is a recorded fact, so
        # there is nothing here that can be stale in §2.12's sense. Stated
        # rather than omitted: `freshness` is required precisely so no endpoint
        # can quietly leave the question open.
        freshness=Freshness(state="RECORDED", observed_at=now),
        page={"count": len(groups), "has_more": False},
    )


def _group(row: OutcomeCounts, group_by: GroupBy) -> dict[str, Any]:
    """One group's record: counts, then the rate, then how much it is worth."""

    record = GroupStats(
        successes=row.successes,
        failures=row.failures,
        expired=row.expired,
        invalidated=row.invalidated,
    )

    rate = record.hit_rate

    return {
        "group_by": group_by.value,
        # Null when the axis *is* the version — the value is already in
        # `algo_version` and repeating it would invite a client to render the
        # same string twice.
        "key": row.key,
        "algo_version": row.algo_version,
        "counts": {
            "resolved": record.resolved,
            "success": record.successes,
            "failed": record.failures,
            # §12.4: reported, not rated. "A scanner that times out constantly
            # has a target-selection problem — visible, not hidden."
            "expired": record.expired,
            "invalidated_early": record.invalidated,
        },
        "hit_rate": {
            "rated": rate.rated,
            # Null, never zero, when nothing was rated: zero is a claim from no
            # evidence.
            "rate_pct": str(rate.rate) if rate.rate is not None else None,
            "confidence_interval": (
                {
                    "level": CONFIDENCE_LEVEL,
                    "low_pct": str(rate.interval.low),
                    "high_pct": str(rate.interval.high),
                }
                if rate.interval is not None
                else None
            ),
            "sufficient_for_inference": rate.sufficient_for_inference,
            # PRD FC-10.1's phrasing, carried in the payload so the honesty is
            # not left to a renderer.
            "label": rate.label,
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
    """§18.8's evidence row: the sealed payload's chain and §15.4's breakdown.

    Read straight out of the sealed payload. §12.1 froze it at publication, and
    the whole point of the row is that a chart can deep-link to the objects a
    claim was made from — which requires the ids that were true then, not the
    ones a fresh detection pass would produce now.

    **The keys here are the sealer's keys, and the first version's were not.**
    It read `payload.get("evidence", {})` where `SealedPayload.as_dict` writes
    `evidence_ids`, so the row returned an empty chain for every signal ever
    sealed — and its `confidence` was the bare number §15.4 forbids, with the
    F1-F6 breakdown sitting unread under `factors`. The test passed because
    its fixture invented an `evidence` key the sealer never writes; the fixture
    is now built by `SealedPayload.as_dict()` itself, so the two cannot drift
    apart again.

    The natural keys (symbol, timeframe) travel beside the ids because §15.2's
    evidence is an "event-id chain": an id alone deep-links to nothing without
    knowing which series it belongs to.
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
            # §15.2's chain, under the sealer's own name.
            "evidence_ids": payload.get("evidence_ids", []),
            # The zone the entry is priced from — the one id on the chain a
            # chart can resolve today (zone ids are stable; see the chart's
            # deep-link note on sweep/swing ids).
            "entry_zone_id": (payload.get("entry_zone") or {}).get("zone_id"),
            # §15.4: confidence "is displayed with its factor breakdown —
            # never as a bare number". Shaped so a client cannot take the
            # number without at least holding the breakdown beside it.
            "confidence": {
                "final": payload.get("confidence"),
                "grade": payload.get("grade"),
                "factors": payload.get("factors", {}),
            },
            "reason": payload.get("reason"),
            "htf_chain": payload.get("htf_chain", {}),
            "risk": payload.get("risk", {}),
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
