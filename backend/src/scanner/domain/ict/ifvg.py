"""Inverse Fair Value Gap detector (SLS §5.5)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.ict.fvg import FairValueGap
from scanner.domain.ict.model import (
    FvgState,
    IfvgState,
    ZoneBand,
    ZonePolarity,
)

_REJECTION_ATR = Decimal("0.3")


@dataclass(frozen=True, slots=True)
class InverseFairValueGap:
    ifvg_id: str
    parent_fvg_id: str
    polarity: ZonePolarity
    band: ZoneBand
    created_index: int
    created_at: datetime
    remaining_age: int
    state: IfvgState = IfvgState.UNPROVEN


def create_ifvg(
    fvg: FairValueGap,
    *,
    inversion_index: int,
    inversion_at: datetime,
) -> InverseFairValueGap:
    """Create an unproven IFVG from an inverted FVG."""

    if fvg.state is not FvgState.INVERTED:
        raise ValueError("IFVG requires parent FVG state INVERTED")

    polarity = (
        ZonePolarity.BEARISH if fvg.polarity is ZonePolarity.BULLISH else ZonePolarity.BULLISH
    )

    age_used = max(
        0,
        inversion_index - fvg.created_index,
    )

    remaining_age = max(
        0,
        fvg.age_limit - age_used,
    )

    return InverseFairValueGap(
        ifvg_id=_build_ifvg_id(
            parent_fvg_id=fvg.fvg_id,
            inversion_index=inversion_index,
            polarity=polarity,
        ),
        parent_fvg_id=fvg.fvg_id,
        polarity=polarity,
        band=fvg.band,
        created_index=inversion_index,
        created_at=inversion_at,
        remaining_age=remaining_age,
    )


def advance_ifvg(
    ifvg: InverseFairValueGap,
    candle: Candle,
    *,
    candle_index: int,
    atr: Decimal,
) -> InverseFairValueGap:
    """Evaluate IFVG activation, death, and expiry."""

    if atr <= 0:
        raise ValueError("atr must be positive")

    if ifvg.state in {
        IfvgState.DEAD,
        IfvgState.EXPIRED,
    }:
        raise ValueError(f"terminal IFVG cannot transition from {ifvg.state.value}")

    age = candle_index - ifvg.created_index

    if age > ifvg.remaining_age:
        return replace(
            ifvg,
            state=IfvgState.EXPIRED,
        )

    if _closes_against_flip(
        candle,
        ifvg,
    ):
        return replace(
            ifvg,
            state=IfvgState.DEAD,
        )

    if ifvg.state is IfvgState.UNPROVEN:
        if _successful_retest(
            candle,
            ifvg,
            atr,
        ):
            return replace(
                ifvg,
                state=IfvgState.FRESH,
            )

        return ifvg

    return ifvg


def _successful_retest(
    candle: Candle,
    ifvg: InverseFairValueGap,
    atr: Decimal,
) -> bool:
    if not _touches(
        candle,
        ifvg.band,
    ):
        return False

    if ifvg.polarity is ZonePolarity.BULLISH:
        closes_far_side = candle.close >= ifvg.band.high

        wick_into_band = min(
            candle.close,
            ifvg.band.high,
        ) - max(
            candle.low,
            ifvg.band.low,
        )
    else:
        closes_far_side = candle.close <= ifvg.band.low

        wick_into_band = min(
            candle.high,
            ifvg.band.high,
        ) - max(
            candle.close,
            ifvg.band.low,
        )

    return (
        closes_far_side
        and max(
            Decimal("0"),
            wick_into_band,
        )
        >= _REJECTION_ATR * atr
    )


def _closes_against_flip(
    candle: Candle,
    ifvg: InverseFairValueGap,
) -> bool:
    if ifvg.polarity is ZonePolarity.BULLISH:
        return candle.close < ifvg.band.low

    return candle.close > ifvg.band.high


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _build_ifvg_id(
    *,
    parent_fvg_id: str,
    inversion_index: int,
    polarity: ZonePolarity,
) -> str:
    raw = "|".join(
        (
            "ifvg",
            parent_fvg_id,
            str(inversion_index),
            polarity.value,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
