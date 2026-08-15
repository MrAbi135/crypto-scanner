"""Liquidity sweep detection (SLS §4.6)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.liquidity.model import (
    LiquidityPool,
    LiquiditySide,
    PoolState,
    SweepEvent,
)


def detect_single_candle_sweep(
    candle: Candle,
    pool: LiquidityPool,
    *,
    candle_index: int,
    atr: Decimal,
    epsilon: Decimal,
) -> SweepEvent | None:
    """Detect immediate penetration + rejection-close sweep."""

    _validate_inputs(
        pool,
        atr=atr,
        epsilon=epsilon,
    )

    level = pool.sweep_level

    if pool.side is LiquiditySide.BSL:
        if candle.high <= level + epsilon:
            return None

        if candle.close >= level:
            return None

        penetration_price = candle.high
        depth = (penetration_price - level) / atr

        gap_sweep = candle.open > level

    else:
        if candle.low >= level - epsilon:
            return None

        if candle.close <= level:
            return None

        penetration_price = candle.low
        depth = (level - penetration_price) / atr

        gap_sweep = candle.open < level

    return SweepEvent(
        pool_id=pool.pool_id,
        side=pool.side,
        liquidity_class=pool.liquidity_class,
        confirmed_at=candle.close_time,
        confirmed_index=candle_index,
        penetration_price=penetration_price,
        reference_level=level,
        close_back_price=candle.close,
        sweep_depth_atr=depth,
        confirmation_window=1,
        gap_sweep=gap_sweep,
    )


def detect_two_candle_sweep(
    penetration_candle: Candle,
    confirmation_candle: Candle,
    pool: LiquidityPool,
    *,
    confirmation_index: int,
    atr: Decimal,
    epsilon: Decimal,
) -> SweepEvent | None:
    """Detect marginal penetration close + next-candle rejection."""

    _validate_inputs(
        pool,
        atr=atr,
        epsilon=epsilon,
    )

    level = pool.sweep_level

    if pool.side is LiquiditySide.BSL:
        if penetration_candle.high <= level + epsilon:
            return None

        if not (level < penetration_candle.close <= level + epsilon):
            return None

        if confirmation_candle.close >= level:
            return None

        penetration_price = penetration_candle.high

        depth = (penetration_price - level) / atr

        gap_sweep = penetration_candle.open > level

    else:
        if penetration_candle.low >= level - epsilon:
            return None

        if not (level - epsilon <= penetration_candle.close < level):
            return None

        if confirmation_candle.close <= level:
            return None

        penetration_price = penetration_candle.low

        depth = (level - penetration_price) / atr

        gap_sweep = penetration_candle.open < level

    return SweepEvent(
        pool_id=pool.pool_id,
        side=pool.side,
        liquidity_class=pool.liquidity_class,
        confirmed_at=confirmation_candle.close_time,
        confirmed_index=confirmation_index,
        penetration_price=penetration_price,
        reference_level=level,
        close_back_price=confirmation_candle.close,
        sweep_depth_atr=depth,
        confirmation_window=2,
        gap_sweep=gap_sweep,
    )


def sweep_reclaimed(
    sweep: SweepEvent,
    candle: Candle,
    *,
    candle_index: int,
) -> SweepEvent:
    """Mark a sweep reclaimed during its 15-candle relevance window."""

    if sweep.reclaimed:
        return sweep

    if candle_index <= sweep.confirmed_index:
        return sweep

    if candle_index > sweep.setup_expiry_index:
        return sweep

    if sweep.side is LiquiditySide.BSL:
        reclaimed = candle.close > sweep.reference_level
    else:
        reclaimed = candle.close < sweep.reference_level

    if not reclaimed:
        return sweep

    return replace(
        sweep,
        reclaimed=True,
    )


def mark_displaced_after(
    sweep: SweepEvent,
    *,
    candle_index: int,
    displacement_in_reversal_direction: bool,
) -> SweepEvent:
    """Record displacement within three candles after confirmation."""

    if sweep.displaced_after:
        return sweep

    if not displacement_in_reversal_direction:
        return sweep

    elapsed = candle_index - sweep.confirmed_index

    if not 1 <= elapsed <= 3:
        return sweep

    return replace(
        sweep,
        displaced_after=True,
    )


def _validate_inputs(
    pool: LiquidityPool,
    *,
    atr: Decimal,
    epsilon: Decimal,
) -> None:
    if pool.state is not PoolState.ACTIVE:
        raise ValueError("sweep detector requires an ACTIVE pool")

    if atr <= 0:
        raise ValueError("atr must be positive")

    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")
