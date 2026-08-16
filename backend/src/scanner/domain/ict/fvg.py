"""Fair Value Gap detector and lifecycle helpers (SLS §5.4)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.ict.model import (
    FvgState,
    ZoneBand,
    ZonePolarity,
)

_FVG_MIN_ATR = Decimal("0.25")
_FVG_MAX_AGE = 200
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class FairValueGap:
    fvg_id: str
    polarity: ZonePolarity
    band: ZoneBand
    consequent_encroachment: Decimal
    created_index: int
    created_at: datetime
    dealing_range_id: str | None
    state: FvgState = FvgState.OPEN
    gap_adjacent: bool = False

    @property
    def age_limit(self) -> int:
        return _FVG_MAX_AGE


def detect_fvg(
    candles: Sequence[Candle],
    index: int,
    *,
    atr: Decimal,
    middle_is_displacement: bool,
    dealing_range_id: str | None = None,
    gap_adjacent: bool = False,
) -> FairValueGap | None:
    """Detect a confirmed FVG where ``index`` is the third closed candle."""

    if index < 2 or index >= len(candles):
        return None

    if atr <= _ZERO:
        return None

    c1 = candles[index - 2]
    c3 = candles[index]

    bullish_gap = c3.low - c1.high
    bearish_gap = c1.low - c3.high

    if bullish_gap > _ZERO:
        if bullish_gap < _FVG_MIN_ATR * atr and not middle_is_displacement:
            return None

        band = ZoneBand(
            low=c1.high,
            high=c3.low,
        )

        return FairValueGap(
            fvg_id=_build_fvg_id(
                symbol=c3.symbol,
                timeframe=c3.timeframe.value,
                polarity=ZonePolarity.BULLISH,
                created_index=index,
                created_at=c3.close_time,
                band=band,
            ),
            polarity=ZonePolarity.BULLISH,
            band=band,
            consequent_encroachment=band.midpoint,
            created_index=index,
            created_at=c3.close_time,
            dealing_range_id=dealing_range_id,
            gap_adjacent=gap_adjacent,
        )

    if bearish_gap > _ZERO:
        if bearish_gap < _FVG_MIN_ATR * atr and not middle_is_displacement:
            return None

        band = ZoneBand(
            low=c3.high,
            high=c1.low,
        )

        return FairValueGap(
            fvg_id=_build_fvg_id(
                symbol=c3.symbol,
                timeframe=c3.timeframe.value,
                polarity=ZonePolarity.BEARISH,
                created_index=index,
                created_at=c3.close_time,
                band=band,
            ),
            polarity=ZonePolarity.BEARISH,
            band=band,
            consequent_encroachment=band.midpoint,
            created_index=index,
            created_at=c3.close_time,
            dealing_range_id=dealing_range_id,
            gap_adjacent=gap_adjacent,
        )

    return None


def advance_fvg(
    fvg: FairValueGap,
    candle: Candle,
    *,
    candle_index: int,
) -> FairValueGap:
    """Apply wick-fill vs close-through FVG lifecycle semantics."""

    if fvg.state in {
        FvgState.FILLED,
        FvgState.INVERTED,
        FvgState.EXPIRED,
    }:
        raise ValueError(f"terminal FVG cannot transition from {fvg.state.value}")

    age = candle_index - fvg.created_index

    if age > _FVG_MAX_AGE:
        return replace(
            fvg,
            state=FvgState.EXPIRED,
        )

    if not _touches(
        candle,
        fvg.band,
    ):
        return fvg

    if _closes_through(
        candle,
        fvg,
    ):
        return replace(
            fvg,
            state=FvgState.INVERTED,
        )

    if _fills_distal_edge(
        candle,
        fvg,
    ):
        return replace(
            fvg,
            state=FvgState.FILLED,
        )

    if _fills_ce(
        candle,
        fvg,
    ):
        return replace(
            fvg,
            state=FvgState.CE_FILLED,
        )

    if fvg.state is FvgState.OPEN:
        return replace(
            fvg,
            state=FvgState.TOUCHED,
        )

    return fvg


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _closes_through(
    candle: Candle,
    fvg: FairValueGap,
) -> bool:
    if fvg.polarity is ZonePolarity.BULLISH:
        return candle.close < fvg.band.low

    return candle.close > fvg.band.high


def _fills_distal_edge(
    candle: Candle,
    fvg: FairValueGap,
) -> bool:
    if fvg.polarity is ZonePolarity.BULLISH:
        return candle.low <= fvg.band.low

    return candle.high >= fvg.band.high


def _fills_ce(
    candle: Candle,
    fvg: FairValueGap,
) -> bool:
    if fvg.polarity is ZonePolarity.BULLISH:
        return candle.low <= fvg.consequent_encroachment

    return candle.high >= fvg.consequent_encroachment


def _build_fvg_id(
    *,
    symbol: str,
    timeframe: str,
    polarity: ZonePolarity,
    created_index: int,
    created_at: datetime,
    band: ZoneBand,
) -> str:
    raw = "|".join(
        (
            "fvg",
            symbol,
            timeframe,
            polarity.value,
            str(created_index),
            created_at.isoformat(),
            str(band.low),
            str(band.high),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
