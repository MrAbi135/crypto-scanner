"""PostgreSQL reads behind §18.8's archive."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from scanner.application.ports.signals import SignalRecord
from scanner.application.ports.track_record import (
    ArchivedSignal,
    GroupBy,
    HistoryFilters,
    HistoryPage,
    OutcomeCounts,
)
from scanner.infrastructure.persistence.signal_models import SignalRow
from scanner.infrastructure.persistence.signal_outcome_models import SignalOutcomeRow
from scanner.shared import Timeframe


class PgTrackRecordRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def history(
        self,
        filters: HistoryFilters,
        *,
        limit: int,
        after: dict[str, str] | None = None,
    ) -> HistoryPage:
        stmt = (
            select(SignalRow, SignalOutcomeRow)
            # LEFT JOIN: the archive is every published signal, resolved or
            # not. An inner join would quietly turn "the archive" into "the
            # closed trades", which is the flattering half.
            .outerjoin(
                SignalOutcomeRow,
                SignalOutcomeRow.signal_id == SignalRow.signal_id,
            )
            .order_by(SignalRow.published_at.desc(), SignalRow.signal_id.desc())
        )

        stmt = _apply(stmt, filters)

        if after is not None:
            cursor = _position(after)

            if cursor is None:
                # A cursor that decoded but does not name this ordering. Treat
                # it as the start rather than raising: it is a client bug, and
                # serving page one is a less confusing answer than a 500.
                pass
            else:
                stmt = stmt.where(tuple_(SignalRow.published_at, SignalRow.signal_id) < cursor)

        # One more than asked for, so `has_more` is a fact rather than a guess.
        # Counting the whole filtered set to answer it would cost a second scan
        # of an append-only table that only grows.
        async with self._sessions() as session:
            found = (await session.execute(stmt.limit(limit + 1))).all()

        rows = tuple(_archived(signal, outcome) for signal, outcome in found[:limit])

        if len(found) <= limit or not rows:
            return HistoryPage(rows=rows, next_position=None)

        last = rows[-1].signal

        return HistoryPage(
            rows=rows,
            next_position={
                "published_at": last.published_at.isoformat(),
                "signal_id": last.signal_id,
            },
        )

    async def outcome_counts(
        self,
        *,
        group_by: GroupBy,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[OutcomeCounts, ...]:
        """One row per (algo_version, axis) pair.

        `algo_version` is in the GROUP BY unconditionally — §18.8 says
        "Version-segmented always", and a hit rate averaged across two
        algorithm versions describes neither of them.

        The counts are conditional sums rather than four queries: one pass over
        the join, and the four numbers are guaranteed to be from the same
        snapshot of the table.
        """
        axis = _AXIS[group_by]

        columns = [SignalRow.algo_version]

        if axis is not None:
            columns.append(axis)

        stmt = (
            select(
                *columns,
                _count_where(SignalOutcomeRow.outcome == "SUCCESS").label("successes"),
                _count_where(SignalOutcomeRow.outcome == "FAILED").label("failures"),
                _count_where(
                    SignalOutcomeRow.outcome.in_(("EXPIRED_ACTIVE", "EXPIRED_UNTOUCHED"))
                ).label("expired"),
                _count_where(SignalOutcomeRow.outcome == "INVALIDATED_EARLY").label("invalidated"),
            )
            # An inner join: a signal with no outcome has not resolved, and a
            # statistics row is about what happened. The archive read is where
            # live signals belong.
            .join(
                SignalOutcomeRow,
                SignalOutcomeRow.signal_id == SignalRow.signal_id,
            )
            # PRD FC-10.1's delisting exclusion, applied here and nowhere else.
            .where(SignalOutcomeRow.excluded_from_stats.is_(False))
            .group_by(*columns)
            .order_by(*columns)
        )

        if since is not None:
            stmt = stmt.where(SignalRow.published_at >= since)

        if until is not None:
            stmt = stmt.where(SignalRow.published_at < until)

        async with self._sessions() as session:
            rows = (await session.execute(stmt)).all()

        return tuple(
            OutcomeCounts(
                algo_version=row[0],
                key=row[1] if axis is not None else None,
                successes=row[-4],
                failures=row[-3],
                expired=row[-2],
                invalidated=row[-1],
            )
            for row in rows
        )


# §18.8's four axes. `VERSION` maps to None because the version is already in
# every group -- asking to group by it means "version alone", not "version
# twice".
_AXIS = {
    GroupBy.ARCHETYPE: SignalRow.archetype,
    GroupBy.GRADE: SignalRow.grade,
    GroupBy.TIMEFRAME: SignalRow.timeframe,
    GroupBy.VERSION: None,
}


def _count_where(condition: ColumnElement[bool]) -> Any:
    """`count(*) filter (where ...)`, spelled portably.

    A conditional sum rather than four separate queries: one pass, and the four
    numbers cannot come from different snapshots of a table that is being
    appended to while they are read.
    """
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _apply(stmt: Select[Any], filters: HistoryFilters) -> Select[Any]:
    """Every filter narrows; none can widen.

    §9: "Filters narrow only — nothing filterable can surface below-floor or
    suppressed content". Nothing here can: T17 holds published signals only,
    because §12.2 writes a suppression to the event log and not to this table.
    That is the structural reason the rule holds, and it is worth saying —
    a future `include_suppressed` filter would break it.
    """
    if filters.outcomes:
        stmt = stmt.where(SignalOutcomeRow.outcome.in_(filters.outcomes))

    if filters.archetypes:
        stmt = stmt.where(SignalRow.archetype.in_(filters.archetypes))

    if filters.grades:
        stmt = stmt.where(SignalRow.grade.in_(filters.grades))

    if filters.timeframes:
        stmt = stmt.where(SignalRow.timeframe.in_(filters.timeframes))

    if filters.symbols:
        stmt = stmt.where(SignalRow.symbol.in_(filters.symbols))

    if filters.algo_versions:
        stmt = stmt.where(SignalRow.algo_version.in_(filters.algo_versions))

    if filters.published_from is not None:
        stmt = stmt.where(SignalRow.published_at >= filters.published_from)

    if filters.published_to is not None:
        # Exclusive upper bound. A caller asking for "up to the 24th" means the
        # end of the 23rd or the whole of the 24th depending on who is asked,
        # and an inclusive bound on a timestamp silently includes 00:00:00 of
        # the next day only when the row lands exactly on it.
        stmt = stmt.where(SignalRow.published_at < filters.published_to)

    return stmt


def _position(after: dict[str, str]) -> tuple[datetime, str] | None:
    try:
        return (datetime.fromisoformat(after["published_at"]), after["signal_id"])
    except (KeyError, ValueError):
        return None


def _archived(row: SignalRow, outcome: SignalOutcomeRow | None) -> ArchivedSignal:
    signal = SignalRecord(
        signal_id=row.signal_id,
        setup_id=row.setup_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        direction=row.direction,
        archetype=row.archetype,
        grade=row.grade,
        final_confidence=row.final_confidence,
        entry_proximal=row.entry_proximal,
        entry_distal=row.entry_distal,
        invalidation_level=row.invalidation_level,
        target_bands=row.target_bands,
        published_at=row.published_at,
        ttl_candles=row.ttl_candles,
        algo_version=row.algo_version,
        param_set_version=row.param_set_version,
        payload=row.payload,
        payload_hash=row.payload_hash,
        dedup_key=row.dedup_key,
    )

    if outcome is None:
        return ArchivedSignal(signal=signal)

    return ArchivedSignal(
        signal=signal,
        outcome=outcome.outcome,
        resolved_at=outcome.resolved_at,
        elapsed_candles=outcome.elapsed_candles,
        mfe_r=outcome.mfe_r,
        mae_r=outcome.mae_r,
        excluded_from_stats=outcome.excluded_from_stats,
    )
