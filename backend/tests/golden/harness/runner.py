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

from scanner.application.detection.confluence_replay import ConfluenceReplayService
from scanner.application.detection.ict_interaction_replay import (
    IctZoneInteractionReplayService,
)
from scanner.application.detection.ict_ob_replay import IctOrderBlockReplayService
from scanner.application.detection.ict_ote_replay import IctOteReplayService
from scanner.application.detection.ict_replay import IctReplayService
from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.application.detection.participation_replay import ParticipationReplayService
from scanner.application.detection.pipeline import DetectionPipeline
from scanner.application.detection.state import SHIFT_NAMESPACE, EngineStateManager
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    StructureShiftReplayService,
)
from tests.golden.harness.canonical import output_hash
from tests.golden.harness.dataset import GoldenDataset
from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryEngineEventRepository,
    InMemoryEngineStateStore,
    InMemoryIctEvidenceRepository,
    InMemoryIctZoneInteractionContextRepository,
    InMemoryIctZoneInteractionRepository,
    InMemoryIctZoneRepository,
    InMemoryIctZoneStateStore,
    InMemoryIctZoneTransitionRepository,
    InMemoryLiquidityPoolRepository,
    InMemoryLiquidityStateStore,
    InMemoryLiquidityTransitionRepository,
    InMemorySetupRepository,
    InMemorySymbolRepository,
    InMemoryTradeAggregateRepository,
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

    if dataset.engine == "participation":
        return await _run_participation(dataset)

    if dataset.engine == "confluence":
        return await _run_confluence(dataset)

    raise ValueError(
        f"{dataset.dataset_id}: unsupported engine {dataset.engine!r}. "
        "Supported engines: structure, liquidity, ict, participation, confluence."
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


async def _run_confluence(dataset: GoldenDataset) -> dict[str, Any]:
    """SLS §8, which means the whole pipeline.

    Confluence scores what the other engines found, so a case for it cannot
    run in isolation: with no swings, no pools and no zones behind it every
    gate fails for want of evidence, and the dataset would be asserting the
    absence of input rather than the doctrine.

    So this builds `DetectionPipeline` itself, with in-memory ports, and calls
    it. Using the production composition rather than re-assembling the nine
    services here is deliberate — a harness that wired them in its own order
    would be a second definition of "run detection", and the one thing the
    pipeline's own docstring insists on is that there is exactly one.
    """
    candles = InMemoryCandleRepository(dataset.candles)
    clock = FixedClock(HARNESS_CLOCK)

    events = InMemoryEngineEventRepository()
    zones = InMemoryIctZoneRepository()
    zone_transitions = InMemoryIctZoneTransitionRepository()
    pools = InMemoryLiquidityPoolRepository()
    pool_transitions = InMemoryLiquidityTransitionRepository()
    interactions = InMemoryIctZoneInteractionRepository()
    setups = InMemorySetupRepository()

    evidence = InMemoryIctEvidenceRepository(events, pool_transitions)

    shift_state = EngineStateManager(
        InMemoryEngineStateStore(),
        namespace=SHIFT_NAMESPACE,
    )

    pipeline = DetectionPipeline(
        StructureReplayService(
            candles,
            events,
            EngineStateManager(InMemoryEngineStateStore()),
            clock,
        ),
        LiquidityReplayService(
            candles,
            pools,
            pool_transitions,
            events,
            InMemoryLiquidityStateStore(),
            clock,
        ),
        StructureShiftReplayService(candles, events, evidence, clock, shift_state),
        IctReplayService(
            candles,
            zones,
            zone_transitions,
            InMemoryIctZoneStateStore(),
            clock,
        ),
        IctOteReplayService(candles, zones, zone_transitions, clock),
        IctOrderBlockReplayService(
            candles,
            zones,
            zone_transitions,
            InMemoryIctZoneStateStore(),
            evidence,
            clock,
        ),
        IctZoneInteractionReplayService(
            candles,
            InMemoryIctZoneInteractionContextRepository(zones, zone_transitions),
            interactions,
        ),
        ParticipationReplayService(candles, events, clock),
        ConfluenceReplayService(
            candles,
            events,
            zones,
            evidence,
            interactions,
            pools,
            InMemoryTradeAggregateRepository(),
            InMemorySymbolRepository(),
            clock,
            shift_state,
            shift_algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
            algo_version=dataset.algo_version,
            setups=setups,
        ),
    )

    report = await pipeline.run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    _assert_unique_event_keys(events)

    confluence = report.confluence

    return {
        "report": {
            "candles": report.structure.candles,
            "trend_state": report.structure_shift.trend_state,
            "htf_state": confluence.htf_state,
            "unreachable": list(confluence.unreachable),
            "events_inserted": confluence.events_inserted,
        },
        # T16, so the modelled record is asserted and not only the in-memory
        # candidate object. The row is written *from* the candidate and there
        # is code in between; the two can disagree.
        "setups": [
            {
                "symbol": row.symbol,
                "direction": row.direction,
                "archetype": row.archetype,
                "base_confidence": row.base_confidence,
                "final_confidence": row.final_confidence,
                "floor_passed": row.floor_passed,
                "gate_results": row.gate_results,
                "factor_scores": row.factor_scores,
                "adjustments": row.adjustments,
                "evidence": row.evidence,
            }
            for row in sorted(
                setups.rows.values(),
                key=lambda r: (r.symbol, r.direction, r.evaluated_at),
            )
        ],
        "candidates": [
            {
                "direction": candidate.direction,
                "gates_passed": candidate.gates_passed,
                "failed_gates": list(candidate.failed_gates),
                "confidence": candidate.confidence,
                "grade": candidate.grade,
                "archetype": candidate.archetype,
                "publishable": candidate.publishable,
                "factors": candidate.factors,
            }
            for candidate in confluence.candidates
        ],
    }


async def _run_participation(dataset: GoldenDataset) -> dict[str, Any]:
    """SLS §6 and §7 -- volume and momentum, in one service.

    The two sections share a replay because they share a reading: §6.2's spike
    and §7.1's score are both pure functions of the same closed candles, and
    splitting them would make a dataset choose which half of one candle's
    participation it wanted to assert.
    """
    events = InMemoryEngineEventRepository()

    service = ParticipationReplayService(
        InMemoryCandleRepository(dataset.candles),
        events,
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
            "volume_spikes": report.volume_spikes,
            "suspect_volume": report.suspect_volume,
            "expansions": report.expansions,
            "contractions": report.contractions,
            "range_expansions": report.range_expansions,
            "compressions": report.compressions,
            "accelerations": report.accelerations,
            "exhaustion_watches": report.exhaustion_watches,
            "events_inserted": report.events_inserted,
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
    """Run the S6 zone engine as the pipeline runs it.

    `DetectionPipeline` documents the order and this follows it exactly:
    `ict -> ote / ob -> interaction`. All four passes share one zone store and
    one transition ledger, which is the point -- the interaction engine runs
    last so it can read what the zone engines wrote in the same pass, and a
    harness that handed it a separate fixture would test the two halves
    against each other rather than against doctrine.

    The order-block pass reads S4/S5 evidence that no zone-only dataset
    produces, so it sees none. That bounds what an OB case can assert to
    SLS 5.1's formation rules; the coverage manifest records the rest as
    blocked rather than letting a green suite imply they are proven.
    """

    candles = InMemoryCandleRepository(dataset.candles)
    clock = FixedClock(HARNESS_CLOCK)
    events = InMemoryEngineEventRepository()

    zones = InMemoryIctZoneRepository()
    transitions = InMemoryIctZoneTransitionRepository()
    interactions = InMemoryIctZoneInteractionRepository()

    report = await IctReplayService(
        candles,
        zones,
        transitions,
        InMemoryIctZoneStateStore(),
        clock,
        algo_version=dataset.algo_version,
    ).run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    ote_report = await IctOteReplayService(
        candles,
        zones,
        transitions,
        clock,
        algo_version=dataset.algo_version,
    ).run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    ob_report = await IctOrderBlockReplayService(
        candles,
        zones,
        transitions,
        InMemoryIctZoneStateStore(),
        InMemoryIctEvidenceRepository(events),
        clock,
        algo_version=dataset.algo_version,
    ).run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    interaction_report = await IctZoneInteractionReplayService(
        candles,
        InMemoryIctZoneInteractionContextRepository(zones, transitions),
        interactions,
        algo_version=dataset.algo_version,
    ).run(
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
                "dealing_ranges": ote_report.dealing_ranges,
                "impulse_legs": ote_report.impulse_legs,
                "otes_detected": ote_report.otes_detected,
                "order_blocks_detected": ob_report.order_blocks_detected,
                "breakers_created": ob_report.breakers_created,
                "mitigations_created": ob_report.mitigations_created,
                "zones_evaluated": interaction_report.zones_evaluated,
                "touches": interaction_report.touches,
                "rejections": interaction_report.rejections,
                "mitigations": interaction_report.mitigations,
                "respects": interaction_report.respects,
                "violations": interaction_report.violations,
                "confirmations": interaction_report.confirmations,
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
                        # Derived zones (IFVG from an inverted FVG, BPR from a
                        # pair) carry their lineage here; the alias map makes
                        # the link readable instead of a sha256.
                        "parent_zone": zone.parent_zone_id,
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
            "interactions": sorted(
                (
                    {
                        "zone": item.zone_id,
                        "zone_type": item.zone_type,
                        "kind": item.kind,
                        "candle_index": item.candle_index,
                        "observed_at": item.observed_at,
                        "close_through": item.close_through,
                    }
                    for item in interactions.interactions
                ),
                key=lambda item: (item["candle_index"], item["kind"], item["zone_type"]),
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
