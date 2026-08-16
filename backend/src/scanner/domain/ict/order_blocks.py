"""Order Block detector and lifecycle helpers (SLS §5.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.ict.displacement import (
    Displacement,
    DisplacementDirection,
)
from scanner.domain.ict.model import (
    ZoneBand,
    ZonePolarity,
    ZoneState,
)

_OB_MAX_RUN = 3
_OB_WINDOW = 5
_OB_MIN_HEIGHT_ATR = Decimal("0.15")
_OB_MAX_HEIGHT_ATR = Decimal("3")
_OB_MAX_AGE = 250


@dataclass(frozen=True, slots=True)
class OrderBlock:
    ob_id: str
    polarity: ZonePolarity
    band: ZoneBand
    refined_band: ZoneBand
    created_index: int
    confirmed_index: int
    created_at: datetime
    grade: str
    origin_swept: bool
    origin_failure_swing: bool
    stale_context: bool
    state: ZoneState = ZoneState.FRESH


def detect_order_block(
    candles: Sequence[Candle],
    *,
    candidate_end_index: int,
    displacement: Displacement,
    atr: Decimal,
    external_structure_break: bool,
    internal_structure_break: bool,
    mss_origin: bool,
    fvg_created: bool,
    origin_swept: bool,
    origin_failure_swing: bool,
    stale_context: bool = False,
) -> OrderBlock | None:
    """Confirm an OB from an opposing candle run and qualifying move."""

    if atr <= 0:
        raise ValueError("atr must be positive")

    if candidate_end_index < 0 or candidate_end_index >= len(candles):
        return None

    displacement_index = displacement.candle_index

    if displacement_index <= candidate_end_index:
        return None

    if displacement_index - candidate_end_index > _OB_WINDOW:
        return None

    if not (external_structure_break or internal_structure_break or fvg_created):
        return None

    polarity = (
        ZonePolarity.BULLISH
        if displacement.direction is DisplacementDirection.BULLISH
        else ZonePolarity.BEARISH
    )

    run_indices = _candidate_run(
        candles,
        candidate_end_index=(candidate_end_index),
        polarity=polarity,
    )

    if not run_indices:
        return None

    run = [candles[index] for index in run_indices]

    band = ZoneBand(
        low=min(candle.low for candle in run),
        high=max(candle.high for candle in run),
    )

    if band.height < _OB_MIN_HEIGHT_ATR * atr:
        return None

    if band.height > _OB_MAX_HEIGHT_ATR * atr:
        return None

    refined_band = ZoneBand(
        low=min(
            min(
                candle.open,
                candle.close,
            )
            for candle in run
        ),
        high=max(
            max(
                candle.open,
                candle.close,
            )
            for candle in run
        ),
    )

    grade = "OB_A" if external_structure_break or mss_origin else "OB_B"

    created_index = run_indices[0]
    created_at = candles[created_index].open_time

    context_candle = candles[displacement_index]

    return OrderBlock(
        ob_id=_build_ob_id(
            symbol=context_candle.symbol,
            timeframe=(context_candle.timeframe.value),
            polarity=polarity,
            created_index=created_index,
            confirmed_index=(displacement_index),
            band=band,
        ),
        polarity=polarity,
        band=band,
        refined_band=refined_band,
        created_index=created_index,
        confirmed_index=(displacement_index),
        created_at=created_at,
        grade=grade,
        origin_swept=origin_swept,
        origin_failure_swing=(origin_failure_swing),
        stale_context=stale_context,
    )


def advance_order_block(
    ob: OrderBlock,
    candle: Candle,
    *,
    candle_index: int,
) -> OrderBlock:
    """Advance an OB using close-confirmed lifecycle rules."""

    if ob.state in {
        ZoneState.INVALIDATED,
        ZoneState.EXPIRED,
    }:
        raise ValueError(f"terminal OB cannot transition from {ob.state.value}")

    age = candle_index - ob.confirmed_index

    if age > _OB_MAX_AGE and ob.state is ZoneState.FRESH:
        return replace(
            ob,
            state=ZoneState.EXPIRED,
        )

    if _violates(
        candle,
        ob,
    ):
        return replace(
            ob,
            state=ZoneState.INVALIDATED,
        )

    if not _touches(
        candle,
        ob.band,
    ):
        return ob

    mitigation_level = ob.refined_band.midpoint

    if _reaches_mitigation(
        candle,
        ob,
        mitigation_level,
    ) and _closes_on_polarity_side(
        candle,
        ob,
    ):
        return replace(
            ob,
            state=ZoneState.MITIGATED,
        )

    if ob.state is ZoneState.FRESH and _closes_on_polarity_side(
        candle,
        ob,
    ):
        return replace(
            ob,
            state=ZoneState.TESTED,
        )

    return ob


def _candidate_run(
    candles: Sequence[Candle],
    *,
    candidate_end_index: int,
    polarity: ZonePolarity,
) -> tuple[int, ...]:
    indices: list[int] = []
    index = candidate_end_index

    while index >= 0 and len(indices) < _OB_MAX_RUN:
        candle = candles[index]
        body_sign = candle.close - candle.open

        opposing = body_sign < 0 if polarity is ZonePolarity.BULLISH else body_sign > 0

        if body_sign == 0:
            indices.append(index)
            index -= 1
            continue

        if not opposing:
            break

        indices.append(index)
        index -= 1

    indices.reverse()

    if not any(candles[item].close != candles[item].open for item in indices):
        return ()

    return tuple(indices)


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _violates(
    candle: Candle,
    ob: OrderBlock,
) -> bool:
    if ob.polarity is ZonePolarity.BULLISH:
        return candle.close < ob.band.low

    return candle.close > ob.band.high


def _closes_on_polarity_side(
    candle: Candle,
    ob: OrderBlock,
) -> bool:
    if ob.polarity is ZonePolarity.BULLISH:
        return candle.close >= ob.band.high

    return candle.close <= ob.band.low


def _reaches_mitigation(
    candle: Candle,
    ob: OrderBlock,
    level: Decimal,
) -> bool:
    if ob.polarity is ZonePolarity.BULLISH:
        return candle.low <= level

    return candle.high >= level


def _build_ob_id(
    *,
    symbol: str,
    timeframe: str,
    polarity: ZonePolarity,
    created_index: int,
    confirmed_index: int,
    band: ZoneBand,
) -> str:
    raw = "|".join(
        (
            "ob",
            symbol,
            timeframe,
            polarity.value,
            str(created_index),
            str(confirmed_index),
            str(band.low),
            str(band.high),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
