"""Tests for Redis resting-liquidity snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
)
from scanner.domain.liquidity import MAX_POOLS
from scanner.infrastructure.redis.liquidity_state import (
    RedisLiquidityStateStore,
)
from scanner.shared import Timeframe


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(
        self,
        key: str,
    ) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
    ) -> None:
        self.data[key] = value

    async def delete(
        self,
        key: str,
    ) -> None:
        self.data.pop(
            key,
            None,
        )


def make_pool(
    *,
    pool_id: str,
    strength: str,
    price: str,
) -> LiquidityPoolRecord:
    now = datetime(
        2026,
        8,
        15,
        10,
        0,
        tzinfo=UTC,
    )

    return LiquidityPoolRecord(
        pool_id=pool_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=Decimal(price),
        band_low=Decimal(price),
        band_high=Decimal(price),
        strength=Decimal(strength),
        state="ACTIVE",
        member_count=1,
        created_index=10,
        created_at=now,
        updated_at=now,
        evidence="{}",
    )


@pytest.mark.asyncio
async def test_snapshot_round_trip_and_strength_order() -> None:
    redis = FakeRedis()
    store = RedisLiquidityStateStore(redis)  # type: ignore[arg-type]

    weak = make_pool(
        pool_id="weak",
        strength="40",
        price="100",
    )

    strong = make_pool(
        pool_id="strong",
        strength="80",
        price="105",
    )

    await store.save(
        "BTCUSDT",
        Timeframe.M5,
        (
            weak,
            strong,
        ),
    )

    snapshot = await store.load(
        "BTCUSDT",
        Timeframe.M5,
    )

    assert snapshot is not None
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.timeframe == "M5"
    assert snapshot.pools[0]["pool_id"] == "strong"
    assert snapshot.pools[1]["pool_id"] == "weak"


@pytest.mark.asyncio
async def test_snapshot_delete() -> None:
    redis = FakeRedis()
    store = RedisLiquidityStateStore(redis)  # type: ignore[arg-type]

    await store.save(
        "BTCUSDT",
        Timeframe.M5,
        (),
    )

    await store.delete(
        "BTCUSDT",
        Timeframe.M5,
    )

    assert (
        await store.load(
            "BTCUSDT",
            Timeframe.M5,
        )
        is None
    )


def test_snapshot_key_requires_symbol() -> None:
    with pytest.raises(ValueError):
        RedisLiquidityStateStore._key(
            "",
            Timeframe.M5,
        )


@pytest.mark.asyncio
async def test_the_published_map_is_bounded_and_keeps_the_strongest() -> None:
    """§4.2 Performance: "bounded (max_pools = 40, evict lowest-strength first)".

    The sort was already the eviction order; only the eviction was missing. On
    the VM this key held 759 pools for a single symbol-TF, and §4.5 re-reads
    the map on every close.
    """
    store = RedisLiquidityStateStore(FakeRedis())  # type: ignore[arg-type]

    # Strength ascending, so the strongest are the ones added last.
    pools = tuple(
        make_pool(pool_id=f"p{index}", strength=str(index), price=str(1000 + index))
        for index in range(1, 61)
    )

    await store.save("BTCUSDT", Timeframe.M5, pools)

    snapshot = await store.load("BTCUSDT", Timeframe.M5)

    assert snapshot is not None
    assert len(snapshot.pools) == MAX_POOLS

    kept = {pool["pool_id"] for pool in snapshot.pools}

    # 60 down to 21 survive; the forty strongest, not the forty newest.
    assert kept == {f"p{index}" for index in range(21, 61)}
