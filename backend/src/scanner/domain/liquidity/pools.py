"""Liquidity-pool construction and deterministic strength scoring."""

from __future__ import annotations

from decimal import Decimal

from scanner.domain.liquidity.model import (
    LiquidityClass,
    LiquidityPool,
    LiquiditySide,
    PoolSource,
    PoolStrength,
)
from scanner.domain.structure import SwingKind, SwingPoint

_HUNDRED = Decimal("100")
_TWENTY_FIVE = Decimal("25")
_THREE = Decimal("3")
_TWO_HUNDRED = Decimal("200")


def score_pool_strength(
    *,
    touches: int,
    timeframe_rank: int,
    max_timeframe_rank: int,
    age_candles: int,
    member_count: int,
) -> PoolStrength:
    """SLS §4.2 exact four-component pool-strength formula."""

    if touches < 0:
        raise ValueError("touches must be non-negative")

    if timeframe_rank < 0:
        raise ValueError("timeframe_rank must be non-negative")

    if max_timeframe_rank <= 0:
        raise ValueError("max_timeframe_rank must be positive")

    if timeframe_rank > max_timeframe_rank:
        raise ValueError("timeframe_rank cannot exceed max_timeframe_rank")

    if age_candles < 0:
        raise ValueError("age_candles must be non-negative")

    if member_count <= 0:
        raise ValueError("member_count must be positive")

    touches_component = _TWENTY_FIVE * Decimal(min(touches, 3)) / _THREE

    timeframe_component = _TWENTY_FIVE * Decimal(timeframe_rank) / Decimal(max_timeframe_rank)

    age_component = _TWENTY_FIVE * Decimal(min(age_candles, 200)) / _TWO_HUNDRED

    if member_count >= 3:
        cluster_factor = Decimal("1")
    elif member_count == 2:
        cluster_factor = Decimal("0.5")
    else:
        cluster_factor = Decimal("0.25")

    cluster_component = _TWENTY_FIVE * cluster_factor

    result = PoolStrength(
        touches_component=touches_component,
        timeframe_component=timeframe_component,
        age_component=age_component,
        cluster_component=cluster_component,
    )

    if result.total > _HUNDRED:
        raise AssertionError("pool strength cannot exceed 100")

    return result


def pool_from_swing(
    swing: SwingPoint,
    *,
    pool_id: str,
    liquidity_class: LiquidityClass,
    touches: int,
    timeframe_rank: int,
    max_timeframe_rank: int,
    age_candles: int,
) -> LiquidityPool:
    side = LiquiditySide.BSL if swing.kind is SwingKind.HIGH else LiquiditySide.SSL

    strength = score_pool_strength(
        touches=touches,
        timeframe_rank=timeframe_rank,
        max_timeframe_rank=max_timeframe_rank,
        age_candles=age_candles,
        member_count=1,
    )

    return LiquidityPool(
        pool_id=pool_id,
        side=side,
        liquidity_class=liquidity_class,
        source=PoolSource.SWING,
        price=swing.price,
        band_low=swing.price,
        band_high=swing.price,
        created_at=swing.open_time,
        created_index=swing.index,
        strength=strength,
        member_count=1,
    )
