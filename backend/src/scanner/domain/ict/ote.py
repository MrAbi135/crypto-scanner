"""Optimal Trade Entry zone detector (SLS §5.8)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum

from scanner.domain.common import Candle
from scanner.domain.ict.model import (
    ZoneBand,
    ZonePolarity,
    ZoneState,
)
from scanner.domain.ict.pd import (
    PdContext,
)

_OTE_MIN = Decimal("0.62")
_OTE_MAX = Decimal("0.79")
_MIN_LEG_ATR = Decimal("2")
_MAX_AGE = 100


class ImpulseDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True, slots=True)
class ImpulseLeg:
    leg_id: str
    direction: ImpulseDirection
    origin_price: Decimal
    extreme_price: Decimal
    origin_index: int
    end_index: int
    confirmed_at: datetime

    @property
    def length(self) -> Decimal:
        return abs(self.extreme_price - self.origin_price)


@dataclass(frozen=True, slots=True)
class OptimalTradeEntry:
    ote_id: str
    leg_id: str
    polarity: ZonePolarity
    band: ZoneBand
    origin_price: Decimal
    extreme_price: Decimal
    created_index: int
    created_at: datetime
    state: ZoneState = ZoneState.FRESH


def detect_ote(
    leg: ImpulseLeg,
    *,
    atr: Decimal,
) -> OptimalTradeEntry | None:
    """Register OTE for a finalized displacement impulse leg."""

    if atr <= 0:
        raise ValueError("atr must be positive")

    if leg.length < _MIN_LEG_ATR * atr:
        return None

    if leg.direction is ImpulseDirection.BULLISH:
        if leg.extreme_price <= leg.origin_price:
            return None

        upper = leg.extreme_price - leg.length * _OTE_MIN

        lower = leg.extreme_price - leg.length * _OTE_MAX

        polarity = ZonePolarity.BULLISH

    else:
        if leg.extreme_price >= leg.origin_price:
            return None

        lower = leg.extreme_price + leg.length * _OTE_MIN

        upper = leg.extreme_price + leg.length * _OTE_MAX

        polarity = ZonePolarity.BEARISH

    band = ZoneBand(
        low=min(
            lower,
            upper,
        ),
        high=max(
            lower,
            upper,
        ),
    )

    return OptimalTradeEntry(
        ote_id=_build_ote_id(
            leg_id=leg.leg_id,
            polarity=polarity,
            band=band,
        ),
        leg_id=leg.leg_id,
        polarity=polarity,
        band=band,
        origin_price=leg.origin_price,
        extreme_price=leg.extreme_price,
        created_index=leg.end_index,
        created_at=leg.confirmed_at,
    )


def advance_ote(
    ote: OptimalTradeEntry,
    candle: Candle,
    *,
    candle_index: int,
    pd_context: PdContext,
    trend_matches: bool,
    leg_end_consumed: bool,
) -> OptimalTradeEntry:
    """Advance OTE state using trend, PD, age, and origin invalidation."""

    if ote.state in {
        ZoneState.INVALIDATED,
        ZoneState.EXPIRED,
    }:
        raise ValueError(f"terminal OTE cannot transition from {ote.state.value}")

    age = candle_index - ote.created_index

    if age > _MAX_AGE:
        return replace(
            ote,
            state=ZoneState.EXPIRED,
        )

    if not trend_matches:
        return replace(
            ote,
            state=ZoneState.INVALIDATED,
        )

    if leg_end_consumed:
        return replace(
            ote,
            state=ZoneState.INVALIDATED,
        )

    if _closes_beyond_origin(
        ote,
        candle,
    ):
        return replace(
            ote,
            state=ZoneState.INVALIDATED,
        )

    if not _touches(
        candle,
        ote.band,
    ):
        return ote

    if not _pd_gate_passes(
        ote,
        pd_context,
    ):
        return ote

    if _reaches_midpoint(
        ote,
        candle,
    ) and _closes_on_polarity_side(
        ote,
        candle,
    ):
        return replace(
            ote,
            state=ZoneState.MITIGATED,
        )

    if ote.state is ZoneState.FRESH and _closes_on_polarity_side(
        ote,
        candle,
    ):
        return replace(
            ote,
            state=ZoneState.TESTED,
        )

    return ote


def _pd_gate_passes(
    ote: OptimalTradeEntry,
    context: PdContext,
) -> bool:
    if ote.polarity is ZonePolarity.BULLISH:
        return context.long_gate

    return context.short_gate


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _reaches_midpoint(
    ote: OptimalTradeEntry,
    candle: Candle,
) -> bool:
    midpoint = ote.band.midpoint

    if ote.polarity is ZonePolarity.BULLISH:
        return candle.low <= midpoint

    return candle.high >= midpoint


def _closes_on_polarity_side(
    ote: OptimalTradeEntry,
    candle: Candle,
) -> bool:
    if ote.polarity is ZonePolarity.BULLISH:
        return candle.close >= ote.band.high

    return candle.close <= ote.band.low


def _closes_beyond_origin(
    ote: OptimalTradeEntry,
    candle: Candle,
) -> bool:
    if ote.polarity is ZonePolarity.BULLISH:
        return candle.close < ote.origin_price

    return candle.close > ote.origin_price


def _build_ote_id(
    *,
    leg_id: str,
    polarity: ZonePolarity,
    band: ZoneBand,
) -> str:
    raw = "|".join(
        (
            "ote",
            leg_id,
            polarity.value,
            str(band.low),
            str(band.high),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
