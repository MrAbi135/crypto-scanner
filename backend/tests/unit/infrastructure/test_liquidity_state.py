"""Tests for Redis resting-liquidity snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
)
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
