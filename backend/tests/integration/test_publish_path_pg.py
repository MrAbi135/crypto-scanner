"""The golden A3 setup, through the real tables, as the application role.

`s8-a3-continuation-pullback-is-recorded-not-published` was the golden in which
a signal was written, into `InMemorySignalRepository`. Since SLS v1.0.20
§15.3(6) its zone-edge invalidation is suppressed instead (Bundle D M3), so
this file now holds the Postgres path to that verdict: no T17 row and no T18
transition, on the first pass and on a second one. The golden run
also hands the engine no transition repository and no incident repository.
Each of those is a place the real publish path differs:

* with no transitions, §10.3's dedup falls back to TTL arithmetic alone, the
  PUBLISHED row §12 needs is never written, and the refresh-merge never runs;
* with no incidents, §15.3(2)'s "feeds fresh" is always true;
* an in-memory row meets none of T16/T17/T18's CHECK constraints, append-only
  triggers or column types.

So a green golden could sit on top of a publish path that fails on Postgres
-- and since the least-privilege cut-over, fails for a role that is not the
owner. This file runs the golden's own candles through the same pipeline with
the four Postgres repositories the engine is wired with
(`runtime/wiring/detection.py`), connected as `scanner_app` with the
operator's grants applied, and holds the result to the in-memory run.

The repository contract itself -- insert-once, dedup ordering, listing order
-- is `test_signal_repository_contract.py`. This file is the end-to-end half.

Requires Docker (testcontainers). Run: pytest -m integration tests/integration
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("testcontainers")

from scanner.application.ports.repositories import IncidentRecord
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.repositories import PgIncidentRepository
from scanner.infrastructure.persistence.setup_repository import PgSetupRepository
from scanner.infrastructure.persistence.signal_repository import PgSignalRepository
from scanner.infrastructure.persistence.signal_transition_repository import (
    PgSignalTransitionRepository,
)
from tests.golden.harness.canonical import canonical_bytes
from tests.golden.harness.dataset import DATASET_ROOT, GoldenDataset, load_dataset
from tests.golden.harness.memory import InMemorySignalRepository
from tests.golden.harness.runner import run_confluence

pytestmark = pytest.mark.integration

_DATASET = (
    DATASET_ROOT / "s8_confluence" / "an-a3-continuation-pullback-is-recorded-not-published.json"
)


def _by_value(value: Any) -> Any:
    """Decimals as their normalised value, recursively.

    Postgres hands a numeric column back at its declared scale, so 70 returns
    as 70.000000000000000000. That is the same number and must not count as a
    divergence -- but every other difference, including a value that storage
    rounded, still must.
    """
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")

    if isinstance(value, dict):
        return {key: _by_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_by_value(item) for item in value]

    return value


def _resymbol(dataset: GoldenDataset, symbol: str) -> GoldenDataset:
    """The same candles under another symbol.

    T17 and T18 are append-only and the container is shared for the session,
    so a second scenario on these candles needs its own symbol -- the dedup
    key and every id embed it.
    """
    return replace(
        dataset,
        symbol=symbol,
        candles=tuple(replace(candle, symbol=symbol) for candle in dataset.candles),
    )


def _postgres(app_engine) -> dict[str, Any]:
    sessions = build_session_factory(app_engine)

    return {
        "setups": PgSetupRepository(sessions),
        "signals": PgSignalRepository(sessions),
        "transitions": PgSignalTransitionRepository(sessions),
        "incidents": PgIncidentRepository(sessions),
    }


async def test_the_golden_zone_edge_setup_is_suppressed_through_postgres_as_the_app_role(
    app_engine,
) -> None:
    """Same candles, real tables, restricted role: the same record comes back,
    and §15.3(6) writes nothing to T17 or T18 on the real path either."""
    dataset = load_dataset(_DATASET)

    memory = await run_confluence(dataset, signals=InMemorySignalRepository())

    # The premise: the in-memory run is the golden. If this fails, the golden
    # test fails too, and the rest of this file has nothing to compare against.
    assert canonical_bytes(memory) == canonical_bytes(dataset.expected)
    assert memory["signals"] == []
    assert [s["detail"]["reasons"] for s in memory["suppressions"]] == [["ZONE_EDGE_INVALIDATION"]]

    repos = _postgres(app_engine)
    postgres = await run_confluence(dataset, **repos)

    assert _by_value(postgres) == _by_value(memory)
    assert [row for row in await repos["signals"].scan() if row.symbol == dataset.symbol] == []


async def test_a_second_pass_on_a_zone_edge_setup_publishes_nothing(app_engine) -> None:
    """With a transition repository the dedup blocker has state to read; a
    zone-edge setup seen again still writes no signal and nothing to merge a
    refresh into (§15.3(6), and no refresh for a zone edge)."""
    dataset = _resymbol(load_dataset(_DATASET), "GOLDENATHREEREFRESH")
    repos = _postgres(app_engine)

    first = await run_confluence(dataset, **repos)
    again = await run_confluence(dataset, **repos)

    assert first["signals"] == [] == again["signals"]

    # T16 is append-only too: the re-evaluated candle does not add a row.
    assert _by_value(again["setups"]) == _by_value(first["setups"])
    assert [row for row in await repos["signals"].scan() if row.symbol == dataset.symbol] == []


async def test_an_open_incident_on_the_symbol_withholds_the_same_signal(app_engine) -> None:
    """§15.3(2): "all feeds fresh at publish moment".

    The golden runs with no incident repository, so its feeds are fresh by
    construction. Here the identical candidate meets an open incident: it is
    still recorded as a publishable setup -- the verdict about the setup does
    not change -- and no signal is written.
    """
    dataset = _resymbol(load_dataset(_DATASET), "GOLDENATHREESTALE")
    repos = _postgres(app_engine)

    await repos["incidents"].record(
        IncidentRecord(
            id="GOLDENATHREESTALE-GAP",
            scope_type="symbol_tf",
            incident_type="gap",
            started_at=dataset.candles[-3].open_time,
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            notes="publish-path integration test",
        )
    )

    result = await run_confluence(dataset, **repos)

    [up] = [c for c in result["candidates"] if c["direction"] == "UP"]
    [setup] = [s for s in result["setups"] if s["direction"] == "UP"]

    assert up["publishable"] is True
    assert setup["floor_passed"] is True
    assert result["signals"] == []
    assert [row for row in await repos["signals"].scan() if row.symbol == dataset.symbol] == []
