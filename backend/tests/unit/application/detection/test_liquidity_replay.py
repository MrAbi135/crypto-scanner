"""Tests for Sprint S5 liquidity replay lifecycle."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.support.builders import pad_for_warmup

from scanner.application.detection.liquidity_replay import (
    LIQUIDITY_ALGO_VERSION,
    LiquidityReplayService,
    _build_pool_id,
    _LevelMap,
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
    wilder_atr_series,
)
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength
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
    candles = pad_for_warmup(
        [
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
    )

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
        wilder_atr_series(candles),
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
    candles = pad_for_warmup(
        [
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
    )

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
        wilder_atr_series(candles),
    )

    second = await service._replay_pool_lifecycle(
        pools.pool,
        candles,
        wilder_atr_series(candles),
    )

    assert first == "SWEPT"
    assert second is None

    assert len(transitions.items) == 1

    assert len(events.items) == 1


@pytest.mark.asyncio
async def test_external_sweep_plus_reversal_displacement_records_a_stop_hunt() -> None:
    """SLS §4.7 composite, wired for the first time.

    The detector has existed and been unit-tested since S5 but had no caller,
    so nothing ever produced a stop hunt. This drives the real service.

    Note the padding candle carries a body of 1. `mean_body_20` is the
    denominator of §5.10's displacement test, and flat padding would make it
    zero, so displacement could never confirm and the composite could never
    fire — the fixture would pass while testing nothing.
    """

    candles = pad_for_warmup(
        [
            make_candle(0, open_="97", high="99", low="97", close="98"),
            # Penetrates the 100 pool and closes back below: a sweep (§4.6).
            make_candle(1, open_="99", high="102", low="98", close="99"),
            # Reversal displacement one candle later, closing far below the
            # penetration candle's midpoint of 100 (§4.7's 50% reclaim).
            make_candle(2, open_="99", high="99", low="94", close="94.5"),
        ]
    )

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

    result = await service._replay_pool_lifecycle(pools.pool, candles, wilder_atr_series(candles))

    assert result == "SWEPT"

    kinds = [event.event_type for event in events.items]

    assert "LIQUIDITY_SWEEP" in kinds
    assert "LIQUIDITY_STOP_HUNT" in kinds, (
        f"expected a stop hunt after the reversal displacement, got {kinds}"
    )

    hunt = next(e for e in events.items if e.event_type == "LIQUIDITY_STOP_HUNT")
    payload = json.loads(hunt.payload)

    # Measured against the PENETRATION candle, per SLS v1.0.4 §4.7.
    assert payload["penetration_high"] == "102"
    assert payload["penetration_low"] == "98"
    assert payload["elapsed_candles"] == 1
    assert payload["failed"] is False


def _service(candles, pools) -> LiquidityReplayService:
    return LiquidityReplayService(
        FakeCandles(candles),  # type: ignore[arg-type]
        pools,  # type: ignore[arg-type]
        FakeTransitions(),
        FakeEvents(),
        FakeSnapshots(),  # type: ignore[arg-type]
        FakeClock(),
    )


def _aged_pool(created_at: datetime) -> LiquidityPoolRecord:
    record = make_pool()

    return replace(record, created_at=created_at, price=Decimal("100000"))


def test_the_same_swing_keeps_its_id_as_the_window_slides() -> None:
    """The window moves one candle per close; the swing does not move with it.

    Keyed on `swing.index`, one level produced a fresh pool row every pass --
    eight ACTIVE BSL pools at exactly 70022 on the VM's BTCUSDT M5, against
    §4.2's "one price zone = one pool per side per TF".
    """
    open_time = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def swing_at(index: int) -> SwingPoint:
        return SwingPoint(
            index=index,
            open_time=open_time,
            price=Decimal("70022"),
            kind=SwingKind.HIGH,
            strength=SwingStrength.EXTERNAL,
        )

    ids = {
        _build_pool_id(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            swing=swing_at(index),
            algo_version=LIQUIDITY_ALGO_VERSION,
        )
        # The same candle as seen by eight consecutive passes.
        for index in range(195, 203)
    }

    assert len(ids) == 1


@pytest.mark.asyncio
async def test_a_pool_older_than_the_window_expires() -> None:
    """§4.2: "ACTIVE -> EXPIRED (age > pool_max_age = 500 candles)".

    This is the case production is made of and the one the old code could not
    reach: it walked from `created_index + 1`, so a pool whose creation candle
    had left the window took the `start_index >= len(candles)` exit and stayed
    ACTIVE for good.
    """
    candles = pad_for_warmup(
        [
            make_candle(0, open_="98", high="99", low="97", close="98"),
            make_candle(1, open_="98", high="99", low="97", close="98"),
        ]
    )

    # 501 candles before the newest close, so age is 501 and the rule asks for
    # more than 500.
    created_at = candles[-1].close_time - timedelta(minutes=5) * 501

    pools = FakePools(_aged_pool(created_at))

    result = await _service(candles, pools)._replay_pool_lifecycle(
        pools.pool,
        candles,
        wilder_atr_series(candles),
    )

    assert result == "EXPIRED"
    assert pools.pool.state == "EXPIRED"


@pytest.mark.asyncio
async def test_a_pool_one_candle_short_of_retirement_survives() -> None:
    """The boundary is `age > 500`, so 500 is still ACTIVE.

    Without this the previous test would also pass an implementation that
    expired every pool whose creation candle is outside the window.
    """
    candles = pad_for_warmup(
        [
            make_candle(0, open_="98", high="99", low="97", close="98"),
            make_candle(1, open_="98", high="99", low="97", close="98"),
        ]
    )

    created_at = candles[-1].close_time - timedelta(minutes=5) * 500

    pools = FakePools(_aged_pool(created_at))

    result = await _service(candles, pools)._replay_pool_lifecycle(
        pools.pool,
        candles,
        wilder_atr_series(candles),
    )

    assert result is None
    assert pools.pool.state == "ACTIVE"


def test_one_level_is_one_pool_however_the_swing_is_classified() -> None:
    """A k=5 external pivot is also a k=2 internal one, confirmed sooner.

    Keyed on strength, the early pass wrote an INTERNAL pool and the promoting
    pass wrote an EXTERNAL one beside it: 5 EXTERNAL and 3 INTERNAL ACTIVE
    pools at exactly 79500 on the VM's BTCUSDT M5. §4.2 allows one.
    """
    open_time = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def swing_of(strength: SwingStrength) -> SwingPoint:
        return SwingPoint(
            index=42,
            open_time=open_time,
            price=Decimal("79500"),
            kind=SwingKind.HIGH,
            strength=strength,
        )

    ids = {
        _build_pool_id(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            swing=swing_of(strength),
            algo_version=LIQUIDITY_ALGO_VERSION,
        )
        for strength in (SwingStrength.INTERNAL, SwingStrength.EXTERNAL)
    }

    assert len(ids) == 1


def _record_at(price: str, *, pool_id: str, side: str = "BSL") -> LiquidityPoolRecord:
    return replace(make_pool(), pool_id=pool_id, side=side, price=Decimal(price))


def test_a_second_level_inside_epsilon_is_absorbed() -> None:
    """§4.2: "one price zone = one pool per side per TF".

    §4.3's clustering only groups the swings handed to one pass, so two swings
    that are not the same swing can still be the same zone.
    """
    levels = _LevelMap([_record_at("100.00", pool_id="a")])

    assert levels.absorbs("BSL", Decimal("100.04"), "b", Decimal("0.05"))


def test_a_level_outside_epsilon_is_its_own_pool() -> None:
    levels = _LevelMap([_record_at("100.00", pool_id="a")])

    assert not levels.absorbs("BSL", Decimal("100.06"), "b", Decimal("0.05"))


def test_the_other_side_of_the_book_is_never_absorbed() -> None:
    """BSL and SSL at one price are two pools: §4.2 bounds it "per side"."""
    levels = _LevelMap([_record_at("100.00", pool_id="a", side="BSL")])

    assert not levels.absorbs("SSL", Decimal("100.00"), "b", Decimal("0.05"))


def test_a_pool_does_not_absorb_itself_on_the_next_pass() -> None:
    """The same pool is re-detected every pass and must keep upserting.

    Comparing price alone, a pool would collide with its own claimed level and
    stop being rewritten -- and its strength and age would stop maturing,
    which is a subtler failure than the duplication this rule exists to stop.
    """
    levels = _LevelMap([_record_at("100.00", pool_id="a")])

    assert not levels.absorbs("BSL", Decimal("100.00"), "a", Decimal("0.05"))
