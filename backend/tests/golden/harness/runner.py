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
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    StructureShiftReplayService,
)
from scanner.application.marketdata.contexts import higher_timeframe
from scanner.application.ports.repositories import IncidentRepository
from scanner.application.ports.setups import SetupRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRepository
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
    InMemorySignalRepository,
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

    if dataset.engine == "structure_shift":
        return await _run_structure_shift(dataset)

    if dataset.engine == "ict_evidence":
        return await _run_ict(dataset, with_evidence=True)

    if dataset.engine == "confluence":
        return await _run_confluence(dataset)

    raise ValueError(
        f"{dataset.dataset_id}: unsupported engine {dataset.engine!r}. Supported engines: "
        "structure, structure_shift, liquidity, ict, participation, confluence."
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


async def _run_structure_shift(dataset: GoldenDataset) -> dict[str, Any]:
    """SLS §3.6 — CHoCH and MSS, on their own.

    `_run_structure` runs `StructureReplayService`, which detects swings and
    breaks and never reaches §3.6; `_run_confluence` reaches it only underneath
    six factors of scoring arithmetic. Between them §3.6 had no assertable
    surface at all, which is why its fourteen rules sat at zero while deleting
    the CAUTION transition broke nothing.

    The evidence repository is real but its stores are empty, so
    `list_liquidity` returns nothing. That is the honest wiring rather than a
    shortcut: §3.6's origin condition is a disjunction — a sweep **or** a
    failure swing — and a dataset that supplies no sweeps is asserting the
    failure-swing branch. Reaching the sweep branch needs the liquidity engine
    in front of this one, which is `_run_confluence`'s job.
    """

    events = InMemoryEngineEventRepository()

    service = StructureShiftReplayService(
        InMemoryCandleRepository(dataset.candles),
        events,
        InMemoryIctEvidenceRepository(events, InMemoryLiquidityTransitionRepository()),
        FixedClock(HARNESS_CLOCK),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
        algo_version=dataset.algo_version,
    )

    report = await service.run(
        dataset.symbol,
        dataset.timeframe,
        dataset.start,
        dataset.end,
    )

    _assert_unique_event_keys(events)

    # §3.6 records a fact ABOUT a prior event by carrying that event's key, so
    # an invalidation payload holds a sha256 of the MSS it demotes. The dataset
    # format's own rule is that "nothing in the `expected` block requires the
    # labeller to compute a hash or an id", and the liquidity runner already
    # answers this by aliasing pool digests; this does the same for event keys.
    aliases = {
        event.event_key: f"event:{event.event_type}@{event.event_at.isoformat()}"
        for event in events.events
    }

    return _apply_aliases(
        {
            "report": {
                "choch_created": report.choch_created,
                "mss_created": report.mss_created,
                "failed_candidates": report.failed_candidates,
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
        },
        aliases,
    )


async def _run_confluence(dataset: GoldenDataset) -> dict[str, Any]:
    return await run_confluence(dataset)


async def run_confluence(
    dataset: GoldenDataset,
    *,
    setups: SetupRepository | None = None,
    signals: SignalRepository | None = None,
    transitions: SignalTransitionRepository | None = None,
    incidents: IncidentRepository | None = None,
) -> dict[str, Any]:
    """SLS §8, which means the whole pipeline.

    The four publish-path repositories can be injected. A golden run leaves
    them at their defaults -- in-memory setups and signals, no transitions,
    no incidents -- and `tests/integration/test_publish_path_pg.py` passes the
    Postgres ones instead, so the same candles can be shown to publish the
    same signal through the real tables. Both reads below go through the
    ports (`list_at`, `scan`) rather than a double's attributes, so the two
    runs are read back the same way.

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
    setups = setups if setups is not None else InMemorySetupRepository()
    signals = signals if signals is not None else InMemorySignalRepository()

    evidence = InMemoryIctEvidenceRepository(events, pool_transitions)

    shift_state = EngineStateManager(
        InMemoryEngineStateStore(),
        namespace=SHIFT_NAMESPACE,
    )

    # A declared HTF is written where the engine reads it -- the §3.7 shift
    # snapshot of the rung above, under the shift engine's own version -- so
    # `_read_htf_state` runs unmodified. The dataset's own timeframe is a
    # different key, and the shift pass below still derives that one itself.
    if dataset.htf_state is not None:
        above = higher_timeframe(dataset.timeframe)

        if above is None:
            raise ValueError(
                f"{dataset.dataset_id}: htf_state declared on {dataset.timeframe.value}, "
                "which has no timeframe above it"
            )

        await shift_state.save(
            StructureEngineState(
                symbol=dataset.symbol,
                timeframe=above.value,
                algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
                trend_state=dataset.htf_state,
            )
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
            evidence,
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
            signals=signals,
            transitions=transitions,
            incidents=incidents,
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
                # The engine records every candidate of a run at the newest
                # candle's open, so this is the whole run's T16 output.
                await setups.list_at(
                    (dataset.symbol,),
                    dataset.timeframe,
                    dataset.candles[-1].open_time,
                ),
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
        # T17, the end of the chain. `publishable` on a candidate is a verdict
        # about the setup; a row here is §15.3 having been evaluated and passed
        # -- payload complete, levels coherent, R >= 1.5, dedup key clear. The
        # two can disagree, and a case that asserted only the first would pass
        # against a publish path that writes nothing. `signal_id` and
        # `payload_hash` are digests of fields already compared.
        "signals": [
            {
                "symbol": row.symbol,
                "direction": row.direction,
                "archetype": row.archetype,
                "grade": row.grade,
                "final_confidence": row.final_confidence,
                "entry_proximal": row.entry_proximal,
                "entry_distal": row.entry_distal,
                "invalidation_level": row.invalidation_level,
                "target_bands": row.target_bands,
                "published_at": row.published_at,
                "ttl_candles": row.ttl_candles,
                "dedup_key": row.dedup_key,
                "payload": json.loads(row.payload),
            }
            for row in await signals.scan()
            # A real T17 holds every other test's signals too.
            if row.symbol == dataset.symbol
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
        InMemoryIctEvidenceRepository(events, transitions),
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


async def _run_ict(dataset: GoldenDataset, *, with_evidence: bool = False) -> dict[str, Any]:
    """Run the S6 zone engine as the pipeline runs it.

    `DetectionPipeline` documents the order and this follows it exactly:
    `ict -> ote / ob -> interaction`. All four passes share one zone store and
    one transition ledger, which is the point -- the interaction engine runs
    last so it can read what the zone engines wrote in the same pass, and a
    harness that handed it a separate fixture would test the two halves
    against each other rather than against doctrine.

    The order-block pass reads S4/S5 evidence that a zone-only dataset does
    not produce. `engine: "ict"` leaves it with none, which bounds an OB case
    to SLS 5.1's formation rules and is why 5.1's grade and lifecycle rules
    stay blocked in the manifest rather than being implied by a green suite.

    `engine: "ict_evidence"` runs the structure and liquidity replays in front
    of the zone passes, into the same event store, so the OB pass sees real
    swings and real sweep transitions. That is the only way to reach SLS 5.2:
    a breaker is promoted from an INVALIDATED order block, and the promotion
    is gated on `origin_swept`, which is a liquidity fact. The two engines are
    kept separate rather than merged so the five zone-only datasets keep
    asserting exactly what they were verified against.
    """

    candles = InMemoryCandleRepository(dataset.candles)
    clock = FixedClock(HARNESS_CLOCK)
    events = InMemoryEngineEventRepository()

    zones = InMemoryIctZoneRepository()
    transitions = InMemoryIctZoneTransitionRepository()
    interactions = InMemoryIctZoneInteractionRepository()
    pool_transitions = InMemoryLiquidityTransitionRepository()

    if with_evidence:
        await StructureReplayService(
            candles,
            events,
            EngineStateManager(InMemoryEngineStateStore()),
            clock,
        ).run(dataset.symbol, dataset.timeframe, dataset.start, dataset.end)

        await LiquidityReplayService(
            candles,
            InMemoryLiquidityPoolRepository(),
            pool_transitions,
            events,
            InMemoryLiquidityStateStore(),
            InMemoryIctEvidenceRepository(events, pool_transitions),
            clock,
        ).run(dataset.symbol, dataset.timeframe, dataset.start, dataset.end)

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
        InMemoryIctEvidenceRepository(events, pool_transitions),
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
