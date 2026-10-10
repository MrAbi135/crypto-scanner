"""Every copy of §12's state list, held to the engine's own `SignalState`.

The list is written out in six places that cannot import one another: the
enum, the T18 and T19 ORM models, migration 025's constraints, the track
record's buckets, and the interim notifier (stdlib-only, outside `src`). Adding
SLS v1.0.15's CLOSED_FLAT meant editing all of them, and the one that was
missed -- the monitor's `_RESOLVED` -- failed silently: the transition was
written and the outcome never was, so a flat close would have vanished from
the track record with no error anywhere.

Each copy therefore gets one assertion here. A new state that is added to the
enum and forgotten in any copy fails this file, not production.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from scanner.domain.lifecycle import TERMINAL_STATES, SignalState

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

ALL_STATES = {s.value for s in SignalState}

# Every terminal state a published signal can reach. SUPPRESSED is terminal
# but belongs to a candidate that never became a signal, so it never gets an
# outcome row, is never monitored and is never pushed.
OUTCOME_STATES = {s.value for s in TERMINAL_STATES} - {SignalState.SUPPRESSED.value}


def _quoted(sql_list: str) -> set[str]:
    return set(re.findall(r"'([A-Z_]+)'", sql_list))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


def test_the_premise_closed_flat_is_an_outcome_state() -> None:
    """Assert the premise first: if CLOSED_FLAT were not terminal, every test
    below would pass for the wrong reason."""
    assert "CLOSED_FLAT" in OUTCOME_STATES


def test_t18_accepts_every_state_the_enum_knows() -> None:
    from scanner.infrastructure.persistence.signal_transition_models import _STATES

    assert _quoted(_STATES) == ALL_STATES


def test_t19_accepts_every_outcome_and_nothing_else() -> None:
    from scanner.infrastructure.persistence.signal_outcome_models import _OUTCOMES

    assert _quoted(_OUTCOMES) == OUTCOME_STATES


def test_migration_025_installs_what_the_models_declare() -> None:
    """The database is built by the migration, not the model. A model that
    allows a state the migration's constraint refuses passes every unit test
    and fails on the first insert."""
    migration = _load(
        BACKEND
        / "src/scanner/infrastructure/persistence/alembic/versions/025_closed_flat_and_realised_r.py",
        "migration_025",
    )

    assert _quoted(migration._NEW_STATES) == ALL_STATES
    assert _quoted(migration._NEW_OUTCOMES) == OUTCOME_STATES


def test_the_track_record_buckets_cover_every_outcome_exactly_once() -> None:
    """An outcome in no bucket is not miscounted -- it is silently dropped from
    `resolved`. One in two buckets is counted twice."""
    from scanner.infrastructure.persistence.track_record_repository import OUTCOME_BUCKETS

    placed = [state for states in OUTCOME_BUCKETS.values() for state in states]

    assert set(placed) == OUTCOME_STATES
    assert len(placed) == len(set(placed)), "an outcome sits in two buckets"


def test_only_successes_and_failures_are_rated() -> None:
    """§12.4 rates two outcomes and reports the rest. Pinned beside the buckets
    because a bucket renamed into the rated pair would quietly rate it."""
    from scanner.domain.lifecycle.outcome import HIT_RATE_STATES

    assert {s.value for s in HIT_RATE_STATES} == {"SUCCESS", "FAILED"}


def test_the_monitor_records_an_outcome_for_every_outcome_state() -> None:
    from scanner.application.detection.signal_monitor import _RESOLVED

    assert {s.value for s in _RESOLVED} == OUTCOME_STATES


def test_the_notifier_closes_the_loop_on_every_outcome_state() -> None:
    """A pushed signal whose terminal state is missing here never gets its
    close-the-loop message: the trader told about an entry is never told it
    ended."""
    notifier = _load(REPO / "ops/notify/notify_signals.py", "interim_notifier_states")

    try:
        assert set(notifier.TERMINAL_STATES) == OUTCOME_STATES
    finally:
        del sys.modules["interim_notifier_states"]
