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

from scanner.application.detection.ict_replay import IctReplayService
from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.application.detection.state import EngineStateManager
from scanner.application.detection.structure_replay import StructureReplayService
from tests.golden.harness.canonical import output_hash
from tests.golden.harness.dataset import GoldenDataset
from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryEngineEventRepository,
    InMemoryEngineStateStore,
    InMemoryIctZoneRepository,
    InMemoryIctZoneStateStore,
    InMemoryIctZoneTransitionRepository,
    InMemoryLiquidityPoolRepository,
    InMemoryLiquidityStateStore,
    InMemoryLiquidityTransitionRepository,
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

    if dataset.engine == "liquidity":
        return await _run_liquidity(dataset)

    if dataset.engine == "ict":
        return await _run_ict(dataset)

    raise ValueError(
        f"{dataset.dataset_id}: unsupported engine {dataset.engine!r}. "
        "Supported engines: structure, liquidity, ict."
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


async def _run_liquidity(dataset: GoldenDataset) -> dict[str, Any]:
    pools = InMemoryLiquidityPoolRepository()
    transitions = InMemoryLiquidityTransitionRepository()
    events = InMemoryEngineEventRepository()

    service = LiquidityReplayService(
        InMemoryCandleRepository(dataset.candles),
        pools,
        transitions,
        events,
        InMemoryLiquidityStateStore(),
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

    # pool_id and transition_id are sha256 digests: deterministic, but not
    # something a human labelling a dataset could write. Aliasing pool ids to
    # their natural key keeps the cross-references in evidence payloads
    # meaningful while leaving the file hand-writable. transition_id is
    # dropped outright — it is derived from fields already compared.
    aliases = {
        pool.pool_id: f"pool:{pool.side}:{pool.created_index}" for pool in pools.pools.values()
    }

    return _apply_aliases(
        {
            "report": {
                "candles": report.candles,
                "internal_pools": report.internal_pools,
                "external_pools": report.external_pools,
                "pools_upserted": report.pools_upserted,
                "active_pools": report.active_pools,
                "sweeps": report.sweeps,
                "broken_pools": report.broken_pools,
                "expired_pools": report.expired_pools,
            },
            "pools": sorted(
                (
                    {
                        "pool": pool.pool_id,
                        "side": pool.side,
                        "liquidity_class": pool.liquidity_class,
                        "source": pool.source,
                        "state": pool.state,
                        "price": pool.price,
                        "band_low": pool.band_low,
                        "band_high": pool.band_high,
                        "strength": pool.strength,
                        "member_count": pool.member_count,
                        "created_index": pool.created_index,
                        "created_at": pool.created_at,
                    }
                    for pool in pools.pools.values()
                ),
                key=lambda item: (item["created_index"], item["side"]),
            ),
            "transitions": sorted(
                (
                    {
                        "pool": transition.pool_id,
                        "from_state": transition.from_state,
                        "to_state": transition.to_state,
                        "reason": transition.reason,
                        "candle_index": transition.candle_index,
                        "transitioned_at": transition.transitioned_at,
                        "evidence": _parse_payload(transition.evidence),
                    }
                    for transition in transitions.transitions
                ),
                key=lambda item: (item["candle_index"], item["to_state"]),
            ),
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
        },
        aliases,
    )


async def _run_ict(dataset: GoldenDataset) -> dict[str, Any]:
    """Run the FVG / IFVG / BPR pass of the S6 zone engine.

    Order-block, OTE and interaction passes are separate services with their
    own ports; they are wired in a later increment. A dataset that expects
    their output will simply not see it, which is why zone-type coverage is
    tracked in the README rather than implied by a green suite.
    """

    zones = InMemoryIctZoneRepository()
    transitions = InMemoryIctZoneTransitionRepository()

    service = IctReplayService(
        InMemoryCandleRepository(dataset.candles),
        zones,
        transitions,
        InMemoryIctZoneStateStore(),
        FixedClock(HARNESS_CLOCK),
        algo_version=dataset.algo_version,
    )

    report = await service.run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    aliases = {
        zone.zone_id: f"zone:{zone.zone_type}:{zone.polarity}:{zone.created_index}"
        for zone in zones.zones.values()
    }

    return _apply_aliases(
        {
            # zones_upserted and the transitions counter are implementation
            # bookkeeping — how many write calls happened — not doctrine, and
            # a labeller cannot derive them from the SLS. The facts they count
            # are compared in full below.
            "report": {
                "candles": report.candles,
                "displacements": report.displacements,
                "fvgs_detected": report.fvgs_detected,
                "ifvgs_created": report.ifvgs_created,
                "bprs_created": report.bprs_created,
                "live_zones": report.live_zones,
            },
            "zones": sorted(
                (
                    {
                        "zone": zone.zone_id,
                        "zone_type": zone.zone_type,
                        "polarity": zone.polarity,
                        "state": zone.state,
                        "grade": zone.grade,
                        "band_low": zone.band_low,
                        "band_high": zone.band_high,
                        "created_index": zone.created_index,
                        "created_at": zone.created_at,
                        "gap_adjacent": zone.gap_adjacent,
                    }
                    for zone in zones.zones.values()
                ),
                key=lambda item: (item["created_index"], item["zone_type"], item["polarity"]),
            ),
            "transitions": sorted(
                (
                    {
                        "zone": transition.zone_id,
                        "zone_type": transition.zone_type,
                        "from_state": transition.from_state,
                        "to_state": transition.to_state,
                        "reason": transition.reason,
                        "candle_index": transition.candle_index,
                        "transitioned_at": transition.transitioned_at,
                    }
                    for transition in transitions.transitions
                ),
                key=lambda item: (item["candle_index"], item["to_state"], item["zone_type"]),
            ),
        },
        aliases,
    )


def _apply_aliases(value: Any, aliases: dict[str, str]) -> Any:
    """Recursively replace opaque digests with their readable aliases."""

    if isinstance(value, str):
        return aliases.get(value, value)

    if isinstance(value, dict):
        return {key: _apply_aliases(item, aliases) for key, item in value.items()}

    if isinstance(value, list):
        return [_apply_aliases(item, aliases) for item in value]

    return value


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
