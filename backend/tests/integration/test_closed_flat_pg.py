"""Migration 025 against a real database: CLOSED_FLAT and `realised_r` land.

The unit suite proves every copy of §12's state list agrees with the enum,
including migration 025's constraint strings -- but a string is not a
constraint. These tests write the new state and the new column through the
real repositories, into a database built by `alembic upgrade head`, so a
migration that declared the right list and installed the wrong one fails
here rather than on the first M5 trade that gives its move back.

Writes go through the application role, not the owner: the engine connects as
`scanner_app`, and a new column the grants did not cover would pass as owner
and be refused in production.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from scanner.application.ports.signal_outcomes import SignalOutcomeRecord
from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.application.ports.signals import SignalRecord
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.signal_outcome_repository import (
    PgSignalOutcomeRepository,
)
from scanner.infrastructure.persistence.signal_repository import PgSignalRepository
from scanner.infrastructure.persistence.signal_transition_repository import (
    PgSignalTransitionRepository,
)
from scanner.shared import Timeframe

pytestmark = pytest.mark.integration

# Its own symbol and year: the container is shared by the whole integration
# session and T17-T19 are append-only, so nothing here may assume an empty table.
SYMBOL = "FLATUSDT"
T0 = datetime(2034, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=5)


def signal(signal_id: str, *, at: datetime = T0) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        setup_id=f"setup-{signal_id}",
        symbol=SYMBOL,
        timeframe=Timeframe.M5,
        direction="UP",
        archetype="A3",
        grade="B",
        final_confidence=Decimal(72),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal("102.96"),
        target_bands=(
            '{"primary":{"low":"120","high":"120","pool_id":"p1"},"secondary":null,'
            '"ladder":{"start_r":"2","step_r":"1","unbounded":true}}'
        ),
        published_at=at,
        ttl_candles=48,
        algo_version="s8-flat-test",
        param_set_version="2026.10.10.1",
        payload='{"invalidation":{"rule":"risk_stop","price":"102.96"}}',
        payload_hash="f" * 64,
        dedup_key=f"{SYMBOL}|{signal_id}",
    )


def transition(
    signal_id: str, n: int, from_state: str | None, to_state: str
) -> SignalTransitionRecord:
    return SignalTransitionRecord(
        transition_id=f"{signal_id}-t{n}",
        signal_id=signal_id,
        from_state=from_state,
        to_state=to_state,
        at_candle_open_time=T0 + n * STEP,
        recorded_at=T0 + n * STEP,
        stress_test=False,
        refresh=False,
        trigger_evidence='{"rungs":"1","realised_r":"0"}',
    )


def outcome(signal_id: str, state: str, realised_r: Decimal | None) -> SignalOutcomeRecord:
    return SignalOutcomeRecord(
        signal_id=signal_id,
        outcome=state,
        resolved_at=T0 + 4 * STEP,
        elapsed_candles=4,
        mfe_r=Decimal("1.2"),
        mae_r=Decimal("0.1"),
        excluded_from_stats=False,
        resolution_evidence="{}",
        realised_r=realised_r,
    )


async def test_a_closed_flat_transition_is_accepted_and_is_the_current_state(
    app_engine,
) -> None:
    sessions = build_session_factory(app_engine)
    await PgSignalRepository(sessions).append(signal("flat-1"))
    transitions = PgSignalTransitionRepository(sessions)

    assert await transitions.append(transition("flat-1", 0, "DETECTED", "PUBLISHED"))
    assert await transitions.append(transition("flat-1", 2, "PUBLISHED", "ACTIVE"))
    assert await transitions.append(transition("flat-1", 4, "ACTIVE", "CLOSED_FLAT"))

    assert await transitions.current_state("flat-1") == "CLOSED_FLAT"


async def test_a_closed_flat_outcome_keeps_a_zero_realised_r_distinct_from_none(
    app_engine,
) -> None:
    """Zero is the whole meaning of CLOSED_FLAT. A column that read it back as
    NULL would make a flat close indistinguishable from a pool-exit signal
    that never had a realised R."""
    sessions = build_session_factory(app_engine)
    await PgSignalRepository(sessions).append(signal("flat-2"))
    outcomes = PgSignalOutcomeRepository(sessions)

    assert await outcomes.append(outcome("flat-2", "CLOSED_FLAT", Decimal(0)))

    stored = await outcomes.get("flat-2")

    assert stored is not None
    assert stored.outcome == "CLOSED_FLAT"
    assert stored.realised_r is not None
    assert stored.realised_r == Decimal(0)


async def test_realised_r_round_trips_exactly_and_stays_null_when_absent(app_engine) -> None:
    sessions = build_session_factory(app_engine)
    signals = PgSignalRepository(sessions)
    outcomes = PgSignalOutcomeRepository(sessions)

    await signals.append(signal("flat-3", at=T0 + STEP))
    await signals.append(signal("flat-4", at=T0 + 2 * STEP))

    # 0.52 / 1.04: the EXPIRED_ACTIVE mark from the domain tests.
    await outcomes.append(outcome("flat-3", "EXPIRED_ACTIVE", Decimal("0.5")))
    await outcomes.append(outcome("flat-4", "SUCCESS", None))

    marked = await outcomes.get("flat-3")
    pool_exit = await outcomes.get("flat-4")

    assert marked is not None and marked.realised_r == Decimal("0.5")
    assert pool_exit is not None and pool_exit.realised_r is None


async def test_the_constraint_still_refuses_a_state_it_does_not_know(engine) -> None:
    """The control: migration 025 replaced the CHECK constraints. Without
    this, the tests above would also pass against a migration that dropped
    them and installed nothing."""
    sessions = build_session_factory(engine)
    await PgSignalRepository(sessions).append(signal("flat-5", at=T0 + 3 * STEP))

    with pytest.raises(IntegrityError):
        await PgSignalOutcomeRepository(sessions).append(
            outcome("flat-5", "CLOSED_FLATISH", Decimal(0))
        )


async def test_the_track_record_counts_a_flat_close_and_never_rates_it(engine) -> None:
    from scanner.application.ports.track_record import GroupBy
    from scanner.domain.lifecycle.track_record import GroupStats
    from scanner.infrastructure.persistence.track_record_repository import (
        PgTrackRecordRepository,
    )

    sessions = build_session_factory(engine)
    signals = PgSignalRepository(sessions)
    outcomes = PgSignalOutcomeRepository(sessions)

    plan = [("rec-0", "SUCCESS", None), ("rec-1", "CLOSED_FLAT", Decimal(0))]

    for i, (name, state, realised) in enumerate(plan):
        await signals.append(
            replace(
                signal(name, at=T0 + timedelta(days=1, minutes=5 * i)),
                algo_version="s8-flat-record",
            )
        )
        await outcomes.append(outcome(name, state, realised))

    rows = [
        r
        for r in await PgTrackRecordRepository(sessions).outcome_counts(group_by=GroupBy.ARCHETYPE)
        if r.algo_version == "s8-flat-record"
    ]

    assert len(rows) == 1
    (row,) = rows
    assert (row.successes, row.failures, row.closed_flat) == (1, 0, 1)

    # The domain does the arithmetic (ports/track_record.py says why); the
    # database's job is to hand it the flat close in its own bucket.
    stats = GroupStats(
        successes=row.successes,
        failures=row.failures,
        expired=row.expired,
        invalidated=row.invalidated,
        closed_flat=row.closed_flat,
    )
    assert stats.resolved == 2, "the flat close must count as resolved"
    assert stats.hit_rate.rated == 1, "and must stay out of the rate"
