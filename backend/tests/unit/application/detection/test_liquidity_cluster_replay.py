"""EQH/EQL clusters through the replay service (SLS §4.2, §4.3).

The domain tests cover the detector. These cover what the service does with it,
which is where the §4.2 dedup rule lives -- and dedup is invisible in a domain
test, because a domain function that returns clusters cannot know a swing pool
was also written for the same level.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_evidence import (
    LiquidityEvidenceRecord,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)
TF = Timeframe.H1

# SLS §1.9 gates detection at 300 closed candles, so a shorter fixture makes
# every assertion below pass on an empty report instead of on the detector.
LEAD = 300

# The two equal highs the fixture is built around, and the dip between them.
PEAK_A = Decimal("110.00")
PEAK_B = Decimal("110.02")


def candle(index: int, open_: float, close: float, high: float, low: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TF,
        open_time=BASE + TF.duration * index,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(10),
        quote_volume=Decimal(1000),
        taker_buy_volume=Decimal(5),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def drifting(count: int, *, start: int = 0) -> list[Candle]:
    """Padding that never repeats an extreme.

    A periodic filler manufactures identical highs and lows and therefore
    clusters of its own, which would make the assertions below measure the
    fixture rather than the detector.
    """
    return [
        candle(
            start + i,
            100 + i * 0.37,
            101 + i * 0.37,
            101.6 + i * 0.38,
            98.6 + i * 0.36,
        )
        for i in range(count)
    ]


def equal_highs_series() -> list[Candle]:
    """Two near-equal highs with a genuine retest between them.

    The padding drifts rather than repeating: a periodic filler manufactures
    identical extremes and therefore clusters of its own, which would make the
    assertions below measure the fixture instead of the detector.
    """
    series = drifting(LEAD)

    shape = [
        (102, 104, 105, 101),
        (104, 106, 107, 103),
        (106, 108, 110.00, 105),  # peak A
        (108, 106, 109, 105),
        (106, 103, 107, 102),
        (103, 100, 104, 99.0),  # the retest
        (100, 102, 103, 99.5),
        (102, 105, 106, 101),
        (105, 108, 110.02, 104),  # peak B
        (108, 105, 109, 104),
        (105, 102, 106, 101),
        (102, 100, 103, 99.8),
    ]

    series += [candle(LEAD + k, *values) for k, values in enumerate(shape)]

    series += [
        candle(
            LEAD + 12 + k,
            100 - k * 0.41,
            100.9 - k * 0.41,
            101.5 - k * 0.39,
            98.7 - k * 0.43,
        )
        for k in range(12)
    ]

    return series


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 19, tzinfo=UTC)


class FakeCandles:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def fetch_series(self, symbol, timeframe, start, end) -> tuple[Candle, ...]:
        return tuple(self._candles)


class CollectingPools:
    """Keeps every pool, because dedup is a claim about the whole set."""

    def __init__(self) -> None:
        self.items: dict[str, LiquidityPoolRecord] = {}

    async def upsert(self, pool: LiquidityPoolRecord) -> None:
        self.items[pool.pool_id] = pool

    async def get(self, pool_id: str) -> LiquidityPoolRecord | None:
        return self.items.get(pool_id)

    async def list_active(self, symbol, timeframe) -> tuple[LiquidityPoolRecord, ...]:
        return tuple(p for p in self.items.values() if p.state == "ACTIVE")

    async def transition(self, pool_id, *, to_state, updated_at) -> bool:
        return False


class FakeTransitions:
    def __init__(self) -> None:
        self.items: list[LiquidityTransitionRecord] = []

    async def append(self, transition: LiquidityTransitionRecord) -> bool:
        self.items.append(transition)
        return True


class FakeEvents:
    def __init__(self) -> None:
        self.items: list[EngineEventRecord] = []

    async def append(self, event: EngineEventRecord) -> bool:
        self.items.append(event)
        return True

    async def exists(self, event_key: str) -> bool:
        return any(item.event_key == event_key for item in self.items)


class FakeEvidence:
    def __init__(self, transitions: FakeTransitions) -> None:
        self._transitions = transitions

    async def list_liquidity(self, symbol, timeframe, start, end):
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
            for item in self._transitions.items
            if start <= item.transitioned_at < end
        )


class FakeSnapshots:
    def __init__(self) -> None:
        self.last_pools: tuple[LiquidityPoolRecord, ...] = ()

    async def save(self, symbol, timeframe, pools) -> None:
        self.last_pools = pools

    async def load(self, symbol, timeframe):
        return None


def build(candles: list[Candle]):
    pools = CollectingPools()
    transitions = FakeTransitions()

    service = LiquidityReplayService(
        FakeCandles(candles),
        pools,
        transitions,
        FakeEvents(),
        FakeSnapshots(),
        FakeEvidence(transitions),
        FakeClock(),
    )

    return service, pools


async def run(service, candles: list[Candle]):
    return await service.run("BTCUSDT", TF, BASE, BASE + TF.duration * (len(candles) + 10))


@pytest.mark.asyncio
async def test_two_equal_highs_become_one_cluster_pool() -> None:
    candles = equal_highs_series()

    service, pools = build(candles)

    report = await run(service, candles)

    assert report.clusters == 1

    cluster_pools = [p for p in pools.items.values() if p.source == "CLUSTER"]

    assert len(cluster_pools) == 1

    pool = cluster_pools[0]

    assert pool.member_count == 2
    assert pool.side == "BSL"
    # §4.2: price is the extreme, band is kept for sweep tolerance.
    assert pool.price == PEAK_B
    assert (pool.band_low, pool.band_high) == (PEAK_A, PEAK_B)


@pytest.mark.asyncio
async def test_the_cluster_replaces_its_members_own_swing_pools() -> None:
    """§4.2's dedup rule: "one price zone = one pool per side per TF".

    Cluster members are within epsilon of each other by construction, so
    keeping their individual swing pools would put two pools on one level. A
    sweep of that level then transitions both, and anything ranking pools
    counts the same liquidity twice.
    """
    candles = equal_highs_series()

    service, pools = build(candles)

    await run(service, candles)

    at_cluster_price = [
        p for p in pools.items.values() if p.side == "BSL" and p.band_high == PEAK_B
    ]

    assert len(at_cluster_price) == 1
    assert at_cluster_price[0].source == "CLUSTER"

    assert not [p for p in pools.items.values() if p.source == "SWING" and p.price == PEAK_A]


@pytest.mark.asyncio
async def test_cluster_membership_moves_the_strength_score() -> None:
    """The point of the whole exercise.

    With no caller for §4.3 every pool carried `member_count=1`, so §4.2's
    `cluster_factor` was 0.25 on all of them and `cluster_component` was
    exactly 6.25 -- a quarter of the score that could not vary.
    """
    candles = equal_highs_series()

    service, pools = build(candles)

    await run(service, candles)

    cluster = next(p for p in pools.items.values() if p.source == "CLUSTER")
    swing = next(p for p in pools.items.values() if p.source == "SWING")

    assert json.loads(cluster.evidence)["strength_components"]["cluster"] == "12.5"
    assert json.loads(swing.evidence)["strength_components"]["cluster"] == "6.25"
    assert cluster.strength > swing.strength


@pytest.mark.asyncio
async def test_the_cluster_pool_carries_its_members_as_evidence() -> None:
    """§4.2: "every component is recomputable from stored evidence"."""
    candles = equal_highs_series()

    service, pools = build(candles)

    await run(service, candles)

    evidence = json.loads(next(p for p in pools.items.values() if p.source == "CLUSTER").evidence)

    assert evidence["member_count"] == 2
    assert len(evidence["member_indices"]) == 2
    # Compared as numbers: Decimal keeps the scale it was parsed with, so the
    # stored "110.0" and the expected "110.00" are the same price spelled twice.
    assert [Decimal(price) for price in evidence["member_prices"]] == [PEAK_A, PEAK_B]
    assert evidence["source"] == "equal_level_cluster"


@pytest.mark.asyncio
async def test_a_series_with_no_equal_extremes_produces_no_clusters() -> None:
    """A detector that fires on ordinary price action is not a detector."""
    candles = drifting(LEAD + 24)

    service, pools = build(candles)

    report = await run(service, candles)

    # Without this the test passes on a warm-up refusal, which proves nothing.
    assert report.warmup_satisfied
    assert report.clusters == 0
    assert report.clustered_swings == 0
    assert not [p for p in pools.items.values() if p.source == "CLUSTER"]


@pytest.mark.asyncio
async def test_replaying_the_same_window_is_stable() -> None:
    """Epsilon is read at each cluster's own confirmation, not the window end.

    A window-end ATR would let the same history cluster on one run and not the
    next, so a stored cluster pool could not be re-derived -- and `member_count`
    would drift, taking §4.2's strength with it.
    """
    candles = equal_highs_series()

    service, pools = build(candles)

    first = await run(service, candles)
    before = dict(pools.items)

    second = await run(service, candles)

    assert first.clusters == second.clusters == 1
    assert set(before) == set(pools.items)

    for pool_id, pool in before.items():
        assert pools.items[pool_id].strength == pool.strength
        assert pools.items[pool_id].member_count == pool.member_count


@pytest.mark.asyncio
async def test_one_price_zone_is_one_pool_per_side() -> None:
    """§4.2's dedup rule, stated as directly as it can be tested.

    A k=5 pivot is necessarily also a k=2 pivot, so every external swing comes
    back out of the internal detector too. Building a pool from each put two on
    one price: on real BTCUSDT H1 all 238 external swings shared a
    (swing_index, side) with an internal pool, so 238 of 758 pools were
    duplicates -- and a sweep of such a level transitions both, so anything
    ranking pools counts the same liquidity twice.
    """
    candles = equal_highs_series()

    service, pools = build(candles)

    await run(service, candles)

    levels = [(p.side, p.price) for p in pools.items.values()]

    assert len(levels) == len(set(levels))


@pytest.mark.asyncio
async def test_a_swing_that_is_external_is_not_also_registered_internal() -> None:
    """§4.1 partitions them: external swings register external levels,
    internal swings register internal ones "(lower weight)"."""
    candles = equal_highs_series()

    service, pools = build(candles)

    await run(service, candles)

    by_index: dict[int, set[str]] = {}

    for pool in pools.items.values():
        evidence = json.loads(pool.evidence)

        if evidence["source"] != "confirmed_swing":
            continue

        by_index.setdefault(evidence["swing_index"], set()).add(evidence["swing_strength"])

    assert by_index, "fixture produced no swing pools, so this proves nothing"
    assert all(len(strengths) == 1 for strengths in by_index.values())
