"""Stop-hunt composite detector (SLS §4.7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from scanner.domain.liquidity.model import (
    LiquidityClass,
    LiquiditySide,
    StopHuntEvent,
    SweepEvent,
)


def detect_stop_hunt(
    sweep: SweepEvent,
    *,
    displacement_id: str,
    displacement_at: datetime,
    displacement_index: int,
    displacement_direction: str,
    displacement_close: Decimal,
    sweep_candle_high: Decimal,
    sweep_candle_low: Decimal,
) -> StopHuntEvent | None:
    """Confirm external sweep + rapid reversal displacement."""

    if sweep.liquidity_class is not LiquidityClass.EXTERNAL:
        return None

    if sweep.reclaimed:
        return None

    elapsed = displacement_index - sweep.confirmed_index

    if not 1 <= elapsed <= 3:
        return None

    if sweep_candle_high < sweep_candle_low:
        raise ValueError("sweep candle high cannot be below low")

    total_range = sweep_candle_high - sweep_candle_low

    if total_range <= 0:
        return None

    midpoint = sweep_candle_low + total_range / Decimal("2")

    if sweep.side is LiquiditySide.BSL:
        if displacement_direction != "DOWN":
            return None

        if displacement_close > midpoint:
            return None

    else:
        if displacement_direction != "UP":
            return None

        if displacement_close < midpoint:
            return None

    return StopHuntEvent(
        sweep_pool_id=sweep.pool_id,
        confirmed_at=displacement_at,
        confirmed_index=displacement_index,
        displacement_id=displacement_id,
        elapsed_candles=elapsed,
    )


def mark_stop_hunt_failed(
    event: StopHuntEvent,
    sweep: SweepEvent,
    *,
    candle_index: int,
    candle_close: Decimal,
    sweep_extreme: Decimal,
) -> StopHuntEvent:
    """Mark stop hunt failed if price reclaims the sweep extreme."""

    if event.failed:
        return event

    elapsed = candle_index - event.confirmed_index

    if not 1 <= elapsed <= 5:
        return event

    if sweep.side is LiquiditySide.BSL:
        failed = candle_close > sweep_extreme
    else:
        failed = candle_close < sweep_extreme

    if not failed:
        return event

    return replace(
        event,
        failed=True,
    )
