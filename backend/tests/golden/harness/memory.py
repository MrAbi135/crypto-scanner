"""In-memory port implementations that let golden datasets drive real services.

The detection services depend on *ports*, not on Postgres or Redis, so a
golden run can feed them from a file and still execute the production code
path byte for byte. That is the point: the harness must not re-implement
doctrine, or it would only ever agree with itself.

Everything here is deliberately strict — no autovivification, no silent
coercion — because a permissive double hides the very bugs a golden dataset
exists to catch.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_evidence import (
    LiquidityEvidenceRecord,
    ShiftEvidenceRecord,
    StructureEvidenceRecord,
)
from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionRecord,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.domain.common import Candle, TradeAggregate
from scanner.domain.volume import WashRiskState
from scanner.shared import Timeframe

_TERMINAL_POOL_STATES = frozenset({"SWEPT", "BROKEN", "EXPIRED"})

# Mirrors _TERMINAL_STATES in PgIctZoneRepository. A zone in any of these is
# permanently done; SLS §5 forbids resurrection and the real ON CONFLICT
# clause enforces it in SQL.
_TERMINAL_ZONE_STATES = frozenset({"INVALIDATED", "EXPIRED", "FILLED", "INVERTED", "DEAD"})


class FixedClock:
    """A clock frozen at one instant.

    Detection output must not depend on wall time. Freezing the clock is how
    the harness proves that: if a detector ever branched on "now", two runs
    would still agree here, but the determinism property would be a lie. The
    canonical form therefore also drops clock-derived fields.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class InMemoryCandleRepository:
    """Serves one immutable, pre-sorted candle series."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = tuple(candles)

    async def bulk_insert(self, candles: Sequence[Candle]) -> int:
        raise NotImplementedError("golden datasets are read-only inputs")

    async def latest_open_time(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> datetime | None:
        series = self._series(symbol, timeframe)
        return series[-1].open_time if series else None

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        return tuple(
            candle for candle in self._series(symbol, timeframe) if start <= candle.open_time < end
        )

    async def count_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int:
        return len(await self.fetch_series(symbol, timeframe, start, end))

    def _series(self, symbol: str, timeframe: Timeframe) -> tuple[Candle, ...]:
        return tuple(
            candle
            for candle in self._candles
            if candle.symbol == symbol and candle.timeframe is timeframe
        )


class InMemoryEngineEventRepository:
    """Records emitted events, mirroring the real ON CONFLICT DO NOTHING key.

    The production table is unique on ``event_key``, and services rely on
    ``append`` returning False for a duplicate to keep replay idempotent. The
    double reproduces exactly that contract.
    """

    def __init__(self) -> None:
        self.events: list[EngineEventRecord] = []
        self._keys: set[str] = set()

    async def append(self, event: EngineEventRecord) -> bool:
        if event.event_key in self._keys:
            return False

        self._keys.add(event.event_key)
        self.events.append(event)
        return True

    async def exists(self, event_key: str) -> bool:
        return event_key in self._keys

    async def list_events(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[EngineEventRecord, ...]:
        """Every engine's output, which is what §8 reads.

        Deliberately unfiltered by type, unlike `IctEvidenceRepository`: the
        confluence engine needs BOS, MSS, sweeps and participation flags as
        well as swings, and a double that filtered would hand it a smaller
        world than production does.
        """
        return tuple(
            sorted(
                (
                    event
                    for event in self.events
                    if event.symbol == symbol
                    and event.timeframe == timeframe
                    and start <= event.event_at < end
                ),
                key=lambda event: (event.event_at, event.event_type, event.event_key),
            )
        )


class InMemoryEngineStateStore:
    """Key/value snapshot store standing in for Redis."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def load(self, context_key: str) -> str | None:
        return self.values.get(context_key)

    async def save(self, context_key: str, payload: str) -> None:
        self.values[context_key] = payload

    async def delete(self, context_key: str) -> None:
        self.values.pop(context_key, None)


class InMemoryLiquidityPoolRepository:
    """Mirrors the SQL semantics of `PgLiquidityPoolRepository` exactly.

    The three behaviours reproduced here are the ones the doctrine leans on,
    and each is verified against real Postgres in
    `tests/integration/test_detection_persistence_pg.py`:

    * ``upsert`` updates the mutable subset **only while the pool is ACTIVE**
      (`ON CONFLICT ... WHERE state = 'ACTIVE'`), so a swept pool cannot be
      revived by a later engine pass — SLS §4.2 calls terminal states
      permanent.
    * ``transition`` moves **only** out of ACTIVE and **only** to a terminal
      state, reporting whether it changed anything.
    * ``list_active`` imposes a total order (strength desc, price asc,
      pool_id asc) so downstream output never depends on insertion order.

    A permissive double here would let the harness bless behaviour the real
    database would reject, which is worse than having no harness at all.
    """

    def __init__(self) -> None:
        self.pools: dict[str, LiquidityPoolRecord] = {}

    async def upsert(self, pool: LiquidityPoolRecord) -> None:
        existing = self.pools.get(pool.pool_id)

        if existing is None:
            self.pools[pool.pool_id] = pool
            return

        if existing.state != "ACTIVE":
            return

        self.pools[pool.pool_id] = replace(
            existing,
            liquidity_class=pool.liquidity_class,
            price=pool.price,
            band_low=pool.band_low,
            band_high=pool.band_high,
            strength=pool.strength,
            member_count=pool.member_count,
            updated_at=pool.updated_at,
            evidence=pool.evidence,
        )

    async def get(self, pool_id: str) -> LiquidityPoolRecord | None:
        return self.pools.get(pool_id)

    async def list_active(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[LiquidityPoolRecord, ...]:
        matching = [
            pool
            for pool in self.pools.values()
            if pool.symbol == symbol and pool.timeframe is timeframe and pool.state == "ACTIVE"
        ]

        return tuple(sorted(matching, key=lambda pool: (-pool.strength, pool.price, pool.pool_id)))

    async def transition(
        self,
        pool_id: str,
        *,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        if to_state not in _TERMINAL_POOL_STATES:
            raise ValueError("pool transition target must be terminal")

        existing = self.pools.get(pool_id)

        if existing is None or existing.state != "ACTIVE":
            return False

        self.pools[pool_id] = replace(existing, state=to_state, updated_at=updated_at)
        return True


class InMemoryLiquidityTransitionRepository:
    """Append-only ledger, idempotent on transition_id like the real table."""

    def __init__(self) -> None:
        self.transitions: list[LiquidityTransitionRecord] = []
        self._ids: set[str] = set()

    async def append(self, transition: LiquidityTransitionRecord) -> bool:
        if transition.transition_id in self._ids:
            return False

        self._ids.add(transition.transition_id)
        self.transitions.append(transition)
        return True


class InMemoryIctZoneRepository:
    """Mirrors `PgIctZoneRepository`, including the resurrection guard.

    `upsert` refuses to touch a zone already in a terminal state, exactly as
    the real `ON CONFLICT ... WHERE NOT state IN (...)` clause does, and
    `transition` matches on the expected `from_state` so a caller holding a
    stale read is told it lost rather than clobbering a newer state. Both are
    proven against real TimescaleDB in
    `tests/integration/test_detection_persistence_pg.py`.
    """

    def __init__(self) -> None:
        self.zones: dict[str, IctZoneRecord] = {}

    async def upsert(self, zone: IctZoneRecord) -> None:
        existing = self.zones.get(zone.zone_id)

        if existing is None:
            self.zones[zone.zone_id] = zone
            return

        if existing.state in _TERMINAL_ZONE_STATES:
            return

        self.zones[zone.zone_id] = replace(
            existing,
            grade=zone.grade,
            band_low=zone.band_low,
            band_high=zone.band_high,
            refined_low=zone.refined_low,
            refined_high=zone.refined_high,
            updated_at=zone.updated_at,
            parent_zone_id=zone.parent_zone_id,
            dealing_range_id=zone.dealing_range_id,
            stale_context=zone.stale_context,
            gap_adjacent=zone.gap_adjacent,
            origin_swept=zone.origin_swept,
            evidence=zone.evidence,
        )

    async def get(self, zone_id: str) -> IctZoneRecord | None:
        return self.zones.get(zone_id)

    async def list_live(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        matching = [
            zone
            for zone in self.zones.values()
            if zone.symbol == symbol
            and zone.timeframe is timeframe
            and zone.state not in _TERMINAL_ZONE_STATES
        ]

        return tuple(
            sorted(
                matching,
                key=lambda zone: (-zone.created_index, zone.zone_type, zone.zone_id),
            )
        )

    async def transition(
        self,
        zone_id: str,
        *,
        from_state: str,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        existing = self.zones.get(zone_id)

        if existing is None or existing.state != from_state:
            return False

        self.zones[zone_id] = replace(existing, state=to_state, updated_at=updated_at)
        return True


class InMemoryIctZoneTransitionRepository:
    """Append-only zone-transition ledger, idempotent on transition_id."""

    def __init__(self) -> None:
        self.transitions: list[IctZoneTransitionRecord] = []
        self._ids: set[str] = set()

    async def append(self, transition: IctZoneTransitionRecord) -> bool:
        if transition.transition_id in self._ids:
            return False

        self._ids.add(transition.transition_id)
        self.transitions.append(transition)
        return True


class InMemoryIctZoneStateStore:
    """Snapshot of the live zone working set, standing in for Redis."""

    def __init__(self) -> None:
        self.snapshots: dict[tuple[str, Timeframe], tuple[IctZoneRecord, ...]] = {}

    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        zones: tuple[IctZoneRecord, ...],
    ) -> None:
        self.snapshots[(symbol, timeframe)] = zones

    async def delete(self, symbol: str, timeframe: Timeframe) -> None:
        self.snapshots.pop((symbol, timeframe), None)


class InMemoryLiquidityStateStore:
    """Snapshot of the active pool set, standing in for Redis."""

    def __init__(self) -> None:
        self.snapshots: dict[tuple[str, Timeframe], tuple[LiquidityPoolRecord, ...]] = {}

    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        pools: tuple[LiquidityPoolRecord, ...],
    ) -> None:
        self.snapshots[(symbol, timeframe)] = pools

    async def delete(self, symbol: str, timeframe: Timeframe) -> None:
        self.snapshots.pop((symbol, timeframe), None)


class InMemoryIctEvidenceRepository:
    """The S4/S5 facts §5.1 and §8 read, taken from the stores that wrote them.

    Mirrors `PgIctEvidenceRepository`'s three queries, including which event
    types each one selects -- `SWING_*`/`STRUCTURE_*` for structure,
    `MSS_*`/`CHOCH_*` for shifts, and the liquidity transition ledger for
    sweeps. Getting those prefixes wrong here would hand the ICT and
    confluence engines a different world than production gives them, and the
    datasets would agree with the harness rather than with doctrine.

    Backed by the live stores rather than a fixture, so a dataset that runs
    the whole pipeline gives its later engines real evidence. A dataset that
    runs the zone passes alone gives them none, which is the honest answer and
    bounds what such a case can assert.
    """

    def __init__(
        self,
        events: InMemoryEngineEventRepository,
        transitions: InMemoryLiquidityTransitionRepository | None = None,
    ) -> None:
        self._events = events
        self._transitions = transitions

    def _in_window(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[EngineEventRecord]:
        return sorted(
            (
                event
                for event in self._events.events
                if event.symbol == symbol
                and event.timeframe == timeframe
                and start <= event.event_at < end
            ),
            key=lambda event: (event.event_at, event.event_type, event.event_key),
        )

    async def list_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[StructureEvidenceRecord, ...]:
        return tuple(
            StructureEvidenceRecord(
                event_type=event.event_type,
                event_at=event.event_at,
                algo_version=event.algo_version,
                payload=event.payload,
            )
            for event in self._in_window(symbol, timeframe, start, end)
            if event.event_type.startswith(("SWING_", "STRUCTURE_"))
        )

    async def list_shifts(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[ShiftEvidenceRecord, ...]:
        records: list[ShiftEvidenceRecord] = []

        for event in self._in_window(symbol, timeframe, start, end):
            if not event.event_type.startswith(("MSS_", "CHOCH_")):
                continue

            payload = json.loads(event.payload)

            choch_index = payload.get("choch_index", payload.get("break_index"))

            if choch_index is None:
                continue

            records.append(
                ShiftEvidenceRecord(
                    event_type=event.event_type,
                    direction=str(payload.get("direction", "")),
                    choch_index=int(choch_index),
                    followthrough_index=int(payload.get("followthrough_index", choch_index)),
                    event_at=event.event_at,
                    payload=event.payload,
                )
            )

        return tuple(records)

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[LiquidityEvidenceRecord, ...]:
        if self._transitions is None:
            return ()

        return tuple(
            LiquidityEvidenceRecord(
                pool_id=item.pool_id,
                from_state=item.from_state,
                to_state=item.to_state,
                reason=item.reason,
                transitioned_at=item.transitioned_at,
                candle_index=item.candle_index,
                evidence=item.evidence,
            )
            for item in sorted(
                (
                    item
                    for item in self._transitions.transitions
                    if item.symbol == symbol
                    and item.timeframe == timeframe
                    and start <= item.transitioned_at < end
                ),
                key=lambda item: (
                    item.candle_index,
                    item.transitioned_at,
                    item.transition_id,
                ),
            )
        )


class InMemoryTradeAggregateRepository:
    """DDD T4 minute buckets. Empty unless a dataset supplies them.

    §6.5's size-skew test then reports None rather than a verdict, which is
    what it does in production until the aggTrade stream has run -- so a
    golden case sees the same behaviour an unstreamed symbol sees.
    """

    def __init__(self, aggregates: Sequence[TradeAggregate] = ()) -> None:
        self.aggregates = list(aggregates)

    async def append_many(self, aggregates: Sequence[TradeAggregate]) -> int:
        self.aggregates.extend(aggregates)

        return len(aggregates)

    async def list_between(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[TradeAggregate, ...]:
        return tuple(
            item for item in self.aggregates if item.symbol == symbol and start <= item.minute < end
        )


class InMemorySymbolRepository:
    """Only the §6.6 tag §8 reads. A golden symbol has no daily history, so it
    carries no wash-risk tag -- and §6.7's cap is therefore not applied, which
    is correct rather than convenient: nothing has evaluated it."""

    def __init__(self, wash_risk: WashRiskState | None = None) -> None:
        self.state = wash_risk or WashRiskState()

    async def get_wash_risk(self, exchange_symbol: str) -> WashRiskState:
        return self.state


class InMemoryIctZoneInteractionContextRepository:
    """SLS 5.9's read side, backed by the stores the zone passes just wrote.

    Deliberately not a separate fixture: the interaction engine runs last in
    the pipeline precisely so it can read what the zone engines produced in
    the same pass, and a harness that fed it its own copy would test the two
    halves against each other rather than against doctrine.
    """

    def __init__(
        self,
        zones: InMemoryIctZoneRepository,
        transitions: InMemoryIctZoneTransitionRepository,
    ) -> None:
        self._zones = zones
        self._transitions = transitions

    async def list_zones(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        return tuple(
            zone
            for zone in self._zones.zones.values()
            if zone.symbol == symbol and zone.timeframe == timeframe
        )

    async def list_transitions(
        self,
        zone_id: str,
    ) -> tuple[IctZoneTransitionRecord, ...]:
        return tuple(t for t in self._transitions.transitions if t.zone_id == zone_id)

    async def list_transitions_for(
        self,
        zone_ids: Sequence[str],
    ) -> dict[str, tuple[IctZoneTransitionRecord, ...]]:
        wanted = set(zone_ids)

        found: dict[str, list[IctZoneTransitionRecord]] = {zone_id: [] for zone_id in wanted}

        for transition in self._transitions.transitions:
            if transition.zone_id in wanted:
                found[transition.zone_id].append(transition)

        return {zone_id: tuple(items) for zone_id, items in found.items()}


class InMemoryIctZoneInteractionRepository:
    """Append-only SLS 5.9 interaction ledger, idempotent on interaction_id."""

    def __init__(self) -> None:
        self.interactions: list[IctZoneInteractionRecord] = []
        self._ids: set[str] = set()

    async def append(self, interaction: IctZoneInteractionRecord) -> bool:
        if interaction.interaction_id in self._ids:
            return False

        self._ids.add(interaction.interaction_id)
        self.interactions.append(interaction)
        return True

    async def append_many(
        self,
        interactions: Sequence[IctZoneInteractionRecord],
    ) -> frozenset[str]:
        written: set[str] = set()

        for interaction in interactions:
            if await self.append(interaction):
                written.add(interaction.interaction_id)

        return frozenset(written)

    async def any_respect_at(
        self,
        symbol: str,
        timeframe: Timeframe,
        observed_at: datetime,
    ) -> bool:
        return any(
            item.symbol == symbol
            and item.timeframe == timeframe
            and item.observed_at == observed_at
            and item.kind == "RESPECT"
            for item in self.interactions
        )

    async def list_for_zone(
        self,
        zone_id: str,
    ) -> tuple[IctZoneInteractionRecord, ...]:
        return tuple(
            sorted(
                (item for item in self.interactions if item.zone_id == zone_id),
                key=lambda item: (item.candle_index, item.interaction_id),
            )
        )
