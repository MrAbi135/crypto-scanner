"""Mitigation Block transformations (SLS §5.3)."""

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
class MitigationBlock:
    mitigation_id: str
    parent_ob_id: str
    polarity: ZonePolarity
    band: ZoneBand
    refined_band: ZoneBand
    created_index: int
    created_at: datetime
    grade: str = "MIT"
    state: ZoneState = ZoneState.FRESH


def create_mitigation_block(
    ob: OrderBlock,
    *,
    invalidation_index: int,
    invalidation_at: datetime,
    displacement: Displacement,
    structure_break: bool,
) -> MitigationBlock | None:
    """Promote qualifying failed OB without origin sweep into mitigation."""

    if ob.state is not ZoneState.INVALIDATED:
        raise ValueError("mitigation requires INVALIDATED parent OB")

    if ob.origin_swept:
        return None

    if not ob.origin_failure_swing:
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

    return MitigationBlock(
        mitigation_id=_build_mitigation_id(
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
    )


def advance_mitigation_block(
    block: MitigationBlock,
    candle: Candle,
) -> MitigationBlock:
    """Apply shared zone lifecycle to mitigation block."""

    if block.state in {
        ZoneState.INVALIDATED,
        ZoneState.EXPIRED,
    }:
        raise ValueError(f"terminal mitigation block cannot transition from {block.state.value}")

    if _violates(
        candle,
        block,
    ):
        return replace(
            block,
            state=ZoneState.INVALIDATED,
        )

    if not _touches(
        candle,
        block.band,
    ):
        return block

    if _reaches_midpoint(
        candle,
        block,
    ) and _closes_on_polarity_side(
        candle,
        block,
    ):
        return replace(
            block,
            state=ZoneState.MITIGATED,
        )

    if block.state is ZoneState.FRESH and _closes_on_polarity_side(
        candle,
        block,
    ):
        return replace(
            block,
            state=ZoneState.TESTED,
        )

    return block


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
    block: MitigationBlock,
) -> bool:
    if block.polarity is ZonePolarity.BULLISH:
        return candle.close < block.band.low

    return candle.close > block.band.high


def _closes_on_polarity_side(
    candle: Candle,
    block: MitigationBlock,
) -> bool:
    if block.polarity is ZonePolarity.BULLISH:
        return candle.close >= block.band.high

    return candle.close <= block.band.low


def _reaches_midpoint(
    candle: Candle,
    block: MitigationBlock,
) -> bool:
    midpoint = block.refined_band.midpoint

    if block.polarity is ZonePolarity.BULLISH:
        return candle.low <= midpoint

    return candle.high >= midpoint


def _build_mitigation_id(
    *,
    parent_ob_id: str,
    invalidation_at: datetime,
    polarity: ZonePolarity,
) -> str:
    raw = "|".join(
        (
            "mitigation",
            parent_ob_id,
            invalidation_at.isoformat(),
            polarity.value,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
