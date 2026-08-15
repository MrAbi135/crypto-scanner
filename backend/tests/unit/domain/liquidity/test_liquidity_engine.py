"""Sprint S5 liquidity-domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.liquidity import (
    LiquidityClass,
    LiquidityPool,
    LiquiditySide,
    PoolSource,
    PoolState,
    PoolStateMachine,
    PoolStrength,
    detect_single_candle_sweep,
    detect_stop_hunt,
    detect_two_candle_sweep,
    mark_stop_hunt_failed,
    score_pool_strength,
    should_expire_pool,
    sweep_reclaimed,
)
from scanner.shared import Timeframe


def make_candle(
    *,
    minute: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=datetime(
            2026,
            8,
            15,
            10,
            minute,
            tzinfo=UTC,
        ),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.STREAM,
    )


def make_pool(
    *,
    side: LiquiditySide,
    price: str = "100",
    liquidity_class: LiquidityClass = LiquidityClass.EXTERNAL,
) -> LiquidityPool:
    value = Decimal(price)

    return LiquidityPool(
        pool_id=f"{side.value}-1",
        side=side,
        liquidity_class=liquidity_class,
        source=PoolSource.SWING,
        price=value,
        band_low=value,
        band_high=value,
        created_at=datetime(
            2026,
            8,
            15,
            9,
            0,
            tzinfo=UTC,
        ),
        created_index=0,
        strength=PoolStrength(
            touches_component=Decimal("10"),
            timeframe_component=Decimal("10"),
            age_component=Decimal("10"),
            cluster_component=Decimal("6.25"),
        ),
    )


def test_strength_formula_is_component_attributed() -> None:
    strength = score_pool_strength(
        touches=3,
        timeframe_rank=6,
        max_timeframe_rank=6,
        age_candles=200,
        member_count=3,
    )

    assert strength.touches_component == Decimal("25")
    assert strength.timeframe_component == Decimal("25")
    assert strength.age_component == Decimal("25")
    assert strength.cluster_component == Decimal("25")
    assert strength.total == Decimal("100")


def test_single_candle_bsl_sweep() -> None:
    event = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="102",
            low="98",
            close="99",
        ),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert event is not None
    assert event.confirmation_window == 1
    assert event.sweep_depth_atr == Decimal("1")
    assert event.side is LiquiditySide.BSL


def test_close_through_is_not_bsl_sweep() -> None:
    event = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="102",
            low="98",
            close="101",
        ),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert event is None


def test_two_candle_bsl_sweep() -> None:
    event = detect_two_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="102",
            low="98",
            close="100.05",
        ),
        make_candle(
            minute=5,
            open_="100",
            high="100.5",
            low="98",
            close="99",
        ),
        make_pool(side=LiquiditySide.BSL),
        confirmation_index=11,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert event is not None
    assert event.confirmation_window == 2


def test_sweep_reclaim_only_inside_expiry_window() -> None:
    event = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="102",
            low="98",
            close="99",
        ),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert event is not None

    updated = sweep_reclaimed(
        event,
        make_candle(
            minute=5,
            open_="99",
            high="102",
            low="98",
            close="101",
        ),
        candle_index=11,
    )

    assert updated.reclaimed is True


def test_terminal_pool_state_cannot_resurrect() -> None:
    machine = PoolStateMachine()

    assert machine.sweep() is PoolState.SWEPT

    with pytest.raises(ValueError):
        machine.break_pool()


def test_pool_expiry_is_strictly_greater_than_500() -> None:
    assert should_expire_pool(age_candles=500) is False
    assert should_expire_pool(age_candles=501) is True


def test_external_bsl_stop_hunt_confirms_on_half_range_reclaim() -> None:
    sweep = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="104",
            low="96",
            close="99",
        ),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert sweep is not None

    event = detect_stop_hunt(
        sweep,
        displacement_id="disp-1",
        displacement_at=datetime(
            2026,
            8,
            15,
            10,
            10,
            tzinfo=UTC,
        ),
        displacement_index=12,
        displacement_direction="DOWN",
        displacement_close=Decimal("99"),
        sweep_candle_high=Decimal("104"),
        sweep_candle_low=Decimal("96"),
    )

    assert event is not None
    assert event.elapsed_candles == 2


def test_internal_sweep_cannot_create_stop_hunt() -> None:
    sweep = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="102",
            low="98",
            close="99",
        ),
        make_pool(
            side=LiquiditySide.BSL,
            liquidity_class=LiquidityClass.INTERNAL,
        ),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert sweep is not None

    event = detect_stop_hunt(
        sweep,
        displacement_id="disp-1",
        displacement_at=datetime(
            2026,
            8,
            15,
            10,
            5,
            tzinfo=UTC,
        ),
        displacement_index=11,
        displacement_direction="DOWN",
        displacement_close=Decimal("98"),
        sweep_candle_high=Decimal("102"),
        sweep_candle_low=Decimal("98"),
    )

    assert event is None


def test_stop_hunt_failure_is_preserved_as_flag() -> None:
    sweep = detect_single_candle_sweep(
        make_candle(
            minute=0,
            open_="99",
            high="104",
            low="96",
            close="99",
        ),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=Decimal("2"),
        epsilon=Decimal("0.1"),
    )

    assert sweep is not None

    event = detect_stop_hunt(
        sweep,
        displacement_id="disp-1",
        displacement_at=datetime(
            2026,
            8,
            15,
            10,
            5,
            tzinfo=UTC,
        ),
        displacement_index=11,
        displacement_direction="DOWN",
        displacement_close=Decimal("98"),
        sweep_candle_high=Decimal("104"),
        sweep_candle_low=Decimal("96"),
    )

    assert event is not None

    failed = mark_stop_hunt_failed(
        event,
        sweep,
        candle_index=13,
        candle_close=Decimal("105"),
        sweep_extreme=Decimal("104"),
    )

    assert failed.failed is True
