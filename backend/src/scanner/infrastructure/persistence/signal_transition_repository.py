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
                trigger_evidence=transition.trigger_evidence,
            )
            # On the natural key, not the id: a replayed candle produces the
            # same verdict with a different id if the id ever stops being
            # derived, and it is the candle that must not be read twice.
            .on_conflict_do_nothing(
                index_elements=[
                    SignalTransitionRow.signal_id,
                    SignalTransitionRow.at_candle_open_time,
                ]
            )
            .returning(SignalTransitionRow.transition_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def current_state(self, signal_id: str) -> str | None:
        stmt = (
            select(SignalTransitionRow.to_state)
            .where(SignalTransitionRow.signal_id == signal_id)
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
            .where(SignalRow.symbol == symbol, SignalRow.timeframe == timeframe)
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
