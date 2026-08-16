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

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.domain.common import Candle
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
