"""Breaker Block transformations (SLS §5.2)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime

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
from scanner.domain.ict.order_blocks import (
    OrderBlock,
)


@dataclass(frozen=True, slots=True)
class BreakerBlock:
    breaker_id: str
    parent_ob_id: str
    polarity: ZonePolarity
    band: ZoneBand
    refined_band: ZoneBand
    created_index: int
    created_at: datetime
    grade: str
    gap_break: bool
    state: ZoneState = ZoneState.FRESH


def create_breaker(
    ob: OrderBlock,
    *,
    invalidation_index: int,
    invalidation_at: datetime,
    displacement: Displacement,
    structure_break: bool,
    gap_break: bool = False,
) -> BreakerBlock | None:
    """Promote an INVALIDATED OB into a breaker when doctrine qualifies."""

    if ob.state is not ZoneState.INVALIDATED:
        raise ValueError("breaker requires INVALIDATED parent OB")

    if not ob.origin_swept:
        return None

    if not structure_break:
        return None

    expected_direction = (
        DisplacementDirection.BULLISH
        if ob.polarity is ZonePolarity.BEARISH
        else DisplacementDirection.BEARISH
    )

    if displacement.direction is not expected_direction:
        return None

    if displacement.candle_index != invalidation_index:
        return None

    polarity = _flip(ob.polarity)

    return BreakerBlock(
        breaker_id=_build_breaker_id(
            parent_ob_id=ob.ob_id,
            invalidation_at=invalidation_at,
            polarity=polarity,
        ),
        parent_ob_id=ob.ob_id,
        polarity=polarity,
        band=ob.band,
        refined_band=ob.refined_band,
        created_index=invalidation_index,
        created_at=invalidation_at,
        grade="BRK_A",
        gap_break=gap_break,
    )


def advance_breaker(
    breaker: BreakerBlock,
    candle: Candle,
) -> BreakerBlock:
    """Apply shared zone lifecycle to a breaker."""

    if breaker.state in {
        ZoneState.INVALIDATED,
        ZoneState.EXPIRED,
    }:
        raise ValueError(f"terminal breaker cannot transition from {breaker.state.value}")

    if _violates(
        candle,
        breaker,
    ):
        return replace(
            breaker,
            state=ZoneState.INVALIDATED,
        )

    if not _touches(
        candle,
        breaker.band,
    ):
        return breaker

    if _reaches_midpoint(
        candle,
        breaker,
    ) and _closes_on_polarity_side(
        candle,
        breaker,
    ):
        return replace(
            breaker,
            state=ZoneState.MITIGATED,
        )

    if breaker.state is ZoneState.FRESH and _closes_on_polarity_side(
        candle,
        breaker,
    ):
        return replace(
            breaker,
            state=ZoneState.TESTED,
        )

    return breaker


def _flip(
    polarity: ZonePolarity,
) -> ZonePolarity:
    return ZonePolarity.BULLISH if polarity is ZonePolarity.BEARISH else ZonePolarity.BEARISH


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _violates(
    candle: Candle,
    breaker: BreakerBlock,
) -> bool:
    if breaker.polarity is ZonePolarity.BULLISH:
        return candle.close < breaker.band.low

    return candle.close > breaker.band.high


def _closes_on_polarity_side(
    candle: Candle,
    breaker: BreakerBlock,
) -> bool:
    if breaker.polarity is ZonePolarity.BULLISH:
        return candle.close >= breaker.band.high

    return candle.close <= breaker.band.low


def _reaches_midpoint(
    candle: Candle,
    breaker: BreakerBlock,
) -> bool:
    midpoint = breaker.refined_band.midpoint

    if breaker.polarity is ZonePolarity.BULLISH:
        return candle.low <= midpoint

    return candle.high >= midpoint


def _build_breaker_id(
    *,
    parent_ob_id: str,
    invalidation_at: datetime,
    polarity: ZonePolarity,
) -> str:
    raw = "|".join(
        (
            "breaker",
            parent_ob_id,
            invalidation_at.isoformat(),
            polarity.value,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
