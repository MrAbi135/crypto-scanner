"""Tests for Sprint S5 liquidity replay lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.detection.liquidity_replay import (
    LiquidityReplayService,
)
from scanner.application.ports.detection import (
    EngineEventRecord,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.infrastructure.redis.liquidity_state import (
    RestingLiquiditySnapshot,
)
from scanner.shared import Timeframe


class FakeClock:
    def now(self) -> datetime:
        return datetime(
            2026,
            8,
            15,
            12,
            0,
            tzinfo=UTC,
        )


class FakeCandles:
    def __init__(
        self,
        candles: list[Candle],
    ) -> None:
        self._candles = candles

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        _ = (
            symbol,
            timeframe,
            start,
            end,
        )
        return tuple(self._candles)


class FakePools:
    def __init__(
        self,
        pool: LiquidityPoolRecord,
    ) -> None:
        self.pool = pool

    async def upsert(
        self,
        pool: LiquidityPoolRecord,
    ) -> None:
        if self.pool.state == "ACTIVE":
            self.pool = LiquidityPoolRecord(
                pool_id=self.pool.pool_id,
                symbol=self.pool.symbol,
                timeframe=self.pool.timeframe,
                side=self.pool.side,
                liquidity_class=(self.pool.liquidity_class),
                source=self.pool.source,
                price=self.pool.price,
                band_low=self.pool.band_low,
                band_high=self.pool.band_high,
                strength=pool.strength,
                state=self.pool.state,
                member_count=(self.pool.member_count),
                created_index=(self.pool.created_index),
                created_at=(self.pool.created_at),
                updated_at=pool.updated_at,
                evidence=pool.evidence,
            )

    async def get(
        self,
        pool_id: str,
    ) -> LiquidityPoolRecord | None:
        if pool_id == self.pool.pool_id:
            return self.pool
        return None

    async def list_active(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[LiquidityPoolRecord, ...]:
        if (
            self.pool.symbol == symbol
            and self.pool.timeframe is timeframe
            and self.pool.state == "ACTIVE"
        ):
            return (self.pool,)

        return ()

    async def transition(
        self,
        pool_id: str,
        *,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        if pool_id != self.pool.pool_id or self.pool.state != "ACTIVE":
            return False

        self.pool = LiquidityPoolRecord(
            pool_id=self.pool.pool_id,
            symbol=self.pool.symbol,
            timeframe=self.pool.timeframe,
            side=self.pool.side,
            liquidity_class=(self.pool.liquidity_class),
            source=self.pool.source,
            price=self.pool.price,
            band_low=self.pool.band_low,
            band_high=self.pool.band_high,
            strength=self.pool.strength,
            state=to_state,
            member_count=(self.pool.member_count),
            created_index=(self.pool.created_index),
            created_at=self.pool.created_at,
            updated_at=updated_at,
            evidence=self.pool.evidence,
        )

        return True


class FakeTransitions:
    def __init__(self) -> None:
        self.items: list[LiquidityTransitionRecord] = []

    async def append(
        self,
        transition: LiquidityTransitionRecord,
    ) -> bool:
        self.items.append(transition)
        return True


class FakeEvents:
    def __init__(self) -> None:
        self.items: list[EngineEventRecord] = []

    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool:
        self.items.append(event)
        return True

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        return any(item.event_key == event_key for item in self.items)


class FakeSnapshots:
    def __init__(self) -> None:
        self.last_pools: tuple[LiquidityPoolRecord, ...] = ()

    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        pools: tuple[LiquidityPoolRecord, ...],
    ) -> None:
        _ = (
            symbol,
            timeframe,
        )
        self.last_pools = pools

    async def load(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> RestingLiquiditySnapshot | None:
        _ = (
            symbol,
            timeframe,
        )
        return None

    async def delete(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> None:
        _ = (
            symbol,
            timeframe,
        )


def make_candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    base = datetime(
        2026,
        8,
        15,
        10,
        0,
        tzinfo=UTC,
    )

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=(base + timedelta(minutes=index * 5)),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.REBUILT,
    )


def make_pool() -> LiquidityPoolRecord:
    return LiquidityPoolRecord(
        pool_id="pool-1",
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=Decimal("100"),
        band_low=Decimal("100"),
        band_high=Decimal("100"),
        strength=Decimal("70"),
        state="ACTIVE",
        member_count=1,
        created_index=0,
        created_at=datetime(
            2026,
            8,
            15,
            10,
            5,
            tzinfo=UTC,
        ),
        updated_at=datetime(
            2026,
            8,
            15,
            10,
            5,
            tzinfo=UTC,
        ),
        evidence="{}",
    )


@pytest.mark.asyncio
async def test_active_bsl_pool_sweep_becomes_terminal() -> None:
    candles = [
        make_candle(
            0,
            open_="98",
            high="99",
            low="97",
            close="98",
        ),
        make_candle(
            1,
            open_="99",
            high="102",
            low="98",
            close="99",
        ),
    ]

    pools = FakePools(make_pool())
    transitions = FakeTransitions()
    events = FakeEvents()
    snapshots = FakeSnapshots()

    service = LiquidityReplayService(
        FakeCandles(candles),  # type: ignore[arg-type]
        pools,  # type: ignore[arg-type]
        transitions,
        events,
        snapshots,  # type: ignore[arg-type]
        FakeClock(),
    )

    result = await service._replay_pool_lifecycle(
        pools.pool,
        candles,
    )

    assert result == "SWEPT"
    assert pools.pool.state == "SWEPT"

    assert len(transitions.items) == 1

    assert transitions.items[0].to_state == "SWEPT"

    assert transitions.items[0].reason == "liquidity_sweep"

    assert len(events.items) == 1

    assert events.items[0].event_type == "LIQUIDITY_SWEEP"


@pytest.mark.asyncio
async def test_terminal_pool_cannot_transition_twice() -> None:
    candles = [
        make_candle(
            0,
            open_="98",
            high="99",
            low="97",
            close="98",
        ),
        make_candle(
            1,
            open_="99",
            high="102",
            low="98",
            close="99",
        ),
    ]

    pools = FakePools(make_pool())

    transitions = FakeTransitions()
    events = FakeEvents()
    snapshots = FakeSnapshots()

    service = LiquidityReplayService(
        FakeCandles(candles),  # type: ignore[arg-type]
        pools,  # type: ignore[arg-type]
        transitions,
        events,
        snapshots,  # type: ignore[arg-type]
        FakeClock(),
    )

    first = await service._replay_pool_lifecycle(
        pools.pool,
        candles,
    )

    second = await service._replay_pool_lifecycle(
        pools.pool,
        candles,
    )

    assert first == "SWEPT"
    assert second is None

    assert len(transitions.items) == 1

    assert len(events.items) == 1
