"""PostgreSQL persistence for T18 `detection.signal_transitions`."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.domain.lifecycle import TERMINAL_STATES
from scanner.infrastructure.persistence.signal_models import SignalRow
from scanner.infrastructure.persistence.signal_transition_models import (
    SignalTransitionRow,
)


class PgSignalTransitionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, transition: SignalTransitionRecord) -> bool:
        stmt = (
            pg_insert(SignalTransitionRow)
            .values(
                transition_id=transition.transition_id,
                signal_id=transition.signal_id,
                from_state=transition.from_state,
                to_state=transition.to_state,
                at_candle_open_time=transition.at_candle_open_time,
                recorded_at=transition.recorded_at,
                stress_test=transition.stress_test,
                refresh=transition.refresh,
                trigger_evidence=transition.trigger_evidence,
            )
            # On the natural key, not the id: a replayed candle produces the
            # same verdict with a different id if the id ever stops being
            # derived, and it is the candle that must not be read twice.
            # `refresh` is part of that key because the monitor and the
            # detector both write on the same closed candle -- see migration
            # 017.
            .on_conflict_do_nothing(
                index_elements=[
                    SignalTransitionRow.signal_id,
                    SignalTransitionRow.at_candle_open_time,
                    SignalTransitionRow.refresh,
                ]
            )
            .returning(SignalTransitionRow.transition_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def current_state(self, signal_id: str) -> str | None:
        """The signal's state now.

        Refresh rows are excluded rather than ordered around. A refresh
        carries `from_state == to_state`, so it looks harmless -- but it lands
        on the same candle as the monitor's verdict, and the tie-break
        between two rows on one candle is `transition_id`, which is a hash.
        A refresh written in the same minute a signal resolved could therefore
        win the ordering and report the signal live again, about half the
        time. §12's state machine is the non-refresh rows; this reads those.
        """

        stmt = (
            select(SignalTransitionRow.to_state)
            .where(
                SignalTransitionRow.signal_id == signal_id,
                SignalTransitionRow.refresh.is_(False),
            )
            # `transition_id` breaks the tie the unique constraint already
            # forbids, so this is deterministic even if that constraint is
            # ever relaxed.
            .order_by(
                SignalTransitionRow.at_candle_open_time.desc(),
                SignalTransitionRow.transition_id.desc(),
            )
            .limit(1)
        )

        async with self._sessions() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_for_signal(self, signal_id: str) -> tuple[SignalTransitionRecord, ...]:
        """One signal's whole history, oldest first.

        Ordered by candle then `refresh` then id: within a candle the state
        change reads before the refresh that accompanied it, which is the order
        they happened in as far as a reader is concerned. `transition_id` only
        breaks a tie the unique constraint already forbids.
        """
        stmt = (
            select(SignalTransitionRow)
            .where(SignalTransitionRow.signal_id == signal_id)
            .order_by(
                SignalTransitionRow.at_candle_open_time.asc(),
                SignalTransitionRow.refresh.asc(),
                SignalTransitionRow.transition_id.asc(),
            )
        )

        async with self._sessions() as session:
            rows = (await session.execute(stmt)).scalars().all()

        return tuple(
            SignalTransitionRecord(
                transition_id=row.transition_id,
                signal_id=row.signal_id,
                from_state=row.from_state,
                to_state=row.to_state,
                at_candle_open_time=row.at_candle_open_time,
                recorded_at=row.recorded_at,
                stress_test=row.stress_test,
                refresh=row.refresh,
                trigger_evidence=row.trigger_evidence,
            )
            for row in rows
        )

    async def list_live(self, symbol: str, timeframe: str) -> tuple[str, ...]:
        """Signal ids on this series whose latest state is not terminal.

        The latest row per signal is found with a window function rather than
        a correlated subquery per signal: the monitor asks this once per
        closed candle for a whole series, and a per-signal query would scale
        with the number of signals ever published rather than with the ones
        still live.
        """
        ranked = (
            select(
                SignalTransitionRow.signal_id,
                SignalTransitionRow.to_state,
                func.row_number()
                .over(
                    partition_by=SignalTransitionRow.signal_id,
                    order_by=(
                        SignalTransitionRow.at_candle_open_time.desc(),
                        SignalTransitionRow.transition_id.desc(),
                    ),
                )
                .label("rank"),
            )
            .join(SignalRow, SignalRow.signal_id == SignalTransitionRow.signal_id)
            .where(
                SignalRow.symbol == symbol,
                SignalRow.timeframe == timeframe,
                # Excluded for the reason `current_state` gives: a refresh on
                # the candle a signal resolved would otherwise outrank the
                # resolution and keep a finished signal in the live set.
                SignalTransitionRow.refresh.is_(False),
            )
            .subquery()
        )

        stmt = (
            select(ranked.c.signal_id)
            .where(
                ranked.c.rank == 1,
                ranked.c.to_state.notin_([s.value for s in TERMINAL_STATES]),
            )
            .order_by(ranked.c.signal_id)
        )

        async with self._sessions() as session:
            return tuple((await session.execute(stmt)).scalars().all())

    async def live_states(self) -> tuple[tuple[str, str], ...]:
        """Every live signal id with the state it currently holds.

        The same window function as `list_live` without the series filter, and
        carrying `to_state` out with the id: §18.4's feed spans every context
        and renders the state, so running the per-series query once per context
        would be both more queries and less information.

        `refresh` rows are excluded for the reason `list_live` gives -- a
        refresh on the candle a signal resolved would otherwise outrank the
        resolution and keep a finished signal on the board.
        """
        ranked = (
            select(
                SignalTransitionRow.signal_id,
                SignalTransitionRow.to_state,
                func.row_number()
                .over(
                    partition_by=SignalTransitionRow.signal_id,
                    order_by=(
                        SignalTransitionRow.at_candle_open_time.desc(),
                        SignalTransitionRow.transition_id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(SignalTransitionRow.refresh.is_(False))
            .subquery()
        )

        stmt = (
            select(ranked.c.signal_id, ranked.c.to_state)
            .where(
                ranked.c.rank == 1,
                ranked.c.to_state.notin_([s.value for s in TERMINAL_STATES]),
            )
            .order_by(ranked.c.signal_id)
        )

        async with self._sessions() as session:
            rows = (await session.execute(stmt)).all()

        return tuple((row[0], row[1]) for row in rows)
