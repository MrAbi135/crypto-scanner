"""Run a golden dataset through the real detection services.

The runner wires production services to in-memory ports and returns the run's
canonical form. It contains no doctrine of its own — every judgement about
what a candle series means comes from `scanner.domain` / `scanner.application`
code, exactly as the engine process would execute it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from scanner.application.detection.state import EngineStateManager
from scanner.application.detection.structure_replay import StructureReplayService
from tests.golden.harness.canonical import output_hash
from tests.golden.harness.dataset import GoldenDataset
from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryEngineEventRepository,
    InMemoryEngineStateStore,
)

# Any instant works; it must merely be constant. See canonical.py on why
# clock-derived fields never reach the comparison.
HARNESS_CLOCK = datetime(2026, 1, 1, tzinfo=UTC)


class DuplicateEventKeyError(AssertionError):
    """Raised when a run emits the same event_key twice.

    The production table is unique on event_key and replay idempotency
    depends on it, so a collision inside a single run is a defect even though
    the in-memory double would silently absorb it.
    """


async def run_dataset(dataset: GoldenDataset) -> dict[str, Any]:
    """Execute a dataset and return its canonical result structure."""

    if dataset.engine == "structure":
        return await _run_structure(dataset)

    raise ValueError(
        f"{dataset.dataset_id}: unsupported engine {dataset.engine!r}. "
        "Liquidity and ICT engines are wired in a later sprint increment."
    )


async def _run_structure(dataset: GoldenDataset) -> dict[str, Any]:
    events = InMemoryEngineEventRepository()

    service = StructureReplayService(
        InMemoryCandleRepository(dataset.candles),
        events,
        EngineStateManager(InMemoryEngineStateStore()),
        FixedClock(HARNESS_CLOCK),
        algo_version=dataset.algo_version,
    )

    report = await service.run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    _assert_unique_event_keys(events)

    return {
        "report": {
            "candles": report.candles,
            "internal_swings": report.internal_swings,
            "external_swings": report.external_swings,
            "classified_events": report.classified_events,
            "events_inserted": report.events_inserted,
            "trend_state": report.trend_state,
        },
        "events": sorted(
            (
                {
                    "event_type": event.event_type,
                    "event_at": event.event_at,
                    "payload": _parse_payload(event.payload),
                }
                for event in events.events
            ),
            key=lambda item: (item["event_at"], item["event_type"]),
        ),
    }


async def run_dataset_hash(dataset: GoldenDataset) -> str:
    """Convenience for determinism checks."""

    return output_hash(await run_dataset(dataset))


def _assert_unique_event_keys(events: InMemoryEngineEventRepository) -> None:
    keys = [event.event_key for event in events.events]

    if len(keys) != len(set(keys)):
        raise DuplicateEventKeyError("run emitted duplicate event_key values")


def _parse_payload(payload: str) -> dict[str, Any]:
    """Parse the stored payload so key order cannot affect comparison."""

    parsed: dict[str, Any] = json.loads(payload)
    return parsed
