"""Balanced Price Range composition (SLS §5.6)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.ict.fvg import FairValueGap
from scanner.domain.ict.model import (
    FvgState,
    ZoneBand,
    ZonePolarity,
)

_BPR_PAIR_AGE = 30


@dataclass(frozen=True, slots=True)
class BalancedPriceRange:
    bpr_id: str
    parent_a_id: str
    parent_b_id: str
    polarity: ZonePolarity
    band: ZoneBand
    created_index: int
    created_at: datetime
    state: str = "FRESH"


def compose_bpr(
    first: FairValueGap,
    second: FairValueGap,
    *,
    current_index: int,
    created_at: datetime,
) -> BalancedPriceRange | None:
    """Compose a BPR from qualifying opposing FVGs."""

    if first.polarity is second.polarity:
        return None

    if first.state not in {
        FvgState.OPEN,
        FvgState.TOUCHED,
    }:
        return None

    if second.state not in {
        FvgState.OPEN,
        FvgState.TOUCHED,
    }:
        return None

    if current_index - first.created_index > _BPR_PAIR_AGE:
        return None

    if current_index - second.created_index > _BPR_PAIR_AGE:
        return None

    if first.dealing_range_id != second.dealing_range_id:
        return None

    overlap_low = max(
        first.band.low,
        second.band.low,
    )

    overlap_high = min(
        first.band.high,
        second.band.high,
    )

    if overlap_high <= overlap_low:
        return None

    overlap = overlap_high - overlap_low

    smaller_band = min(
        first.band.height,
        second.band.height,
    )

    if smaller_band <= 0:
        return None

    if overlap / smaller_band < Decimal("0.5"):
        return None

    later = second if second.created_index >= first.created_index else first

    band = ZoneBand(
        low=overlap_low,
        high=overlap_high,
    )

    return BalancedPriceRange(
        bpr_id=_build_bpr_id(
            first_id=first.fvg_id,
            second_id=second.fvg_id,
            created_index=current_index,
            band=band,
        ),
        parent_a_id=first.fvg_id,
        parent_b_id=second.fvg_id,
        polarity=later.polarity,
        band=band,
        created_index=current_index,
        created_at=created_at,
    )


def advance_bpr(
    bpr: BalancedPriceRange,
    candle: Candle,
) -> BalancedPriceRange:
    """Invalidate BPR on close through far edge against polarity."""

    if bpr.state == "DEAD":
        raise ValueError("terminal BPR cannot transition")

    if bpr.polarity is ZonePolarity.BULLISH:
        violated = candle.close < bpr.band.low
    else:
        violated = candle.close > bpr.band.high

    if not violated:
        return bpr

    return replace(
        bpr,
        state="DEAD",
    )


def _build_bpr_id(
    *,
    first_id: str,
    second_id: str,
    created_index: int,
    band: ZoneBand,
) -> str:
    parents = sorted(
        (
            first_id,
            second_id,
        )
    )

    raw = "|".join(
        (
            "bpr",
            parents[0],
            parents[1],
            str(created_index),
            str(band.low),
            str(band.high),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
