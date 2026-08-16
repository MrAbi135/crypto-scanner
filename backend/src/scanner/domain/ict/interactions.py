"""Uniform ICT zone interaction grammar (SLS §5.9)."""

from __future__ import annotations

from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.ict.model import (
    InteractionKind,
    ZoneBand,
    ZoneInteraction,
    ZonePolarity,
)

_REJECTION_WICK_ATR = Decimal("0.3")


def evaluate_zone_interaction(
    candle: Candle,
    *,
    candle_index: int,
    band: ZoneBand,
    polarity: ZonePolarity,
    atr: Decimal,
) -> tuple[ZoneInteraction, ...]:
    if atr <= 0:
        raise ValueError("atr must be positive")

    if not _touches(
        candle,
        band,
    ):
        return ()

    close_through = _violates(
        candle,
        band,
        polarity,
    )

    penetration_depth = _penetration_depth(
        candle,
        band,
        polarity,
    )

    rejection_wick = _rejection_wick(
        candle,
        band,
        polarity,
    )

    interactions: list[ZoneInteraction] = [
        ZoneInteraction(
            kind=InteractionKind.TOUCH,
            candle_index=candle_index,
            observed_at=candle.close_time,
            penetration_depth=penetration_depth,
            close_price=candle.close,
            rejection_wick=rejection_wick,
            close_through=close_through,
        )
    ]

    if close_through:
        interactions.append(
            ZoneInteraction(
                kind=InteractionKind.VIOLATION,
                candle_index=candle_index,
                observed_at=candle.close_time,
                penetration_depth=penetration_depth,
                close_price=candle.close,
                rejection_wick=rejection_wick,
                close_through=True,
            )
        )
        return tuple(interactions)

    rejection = (
        _closes_on_polarity_side(
            candle,
            band,
            polarity,
        )
        and rejection_wick >= _REJECTION_WICK_ATR * atr
    )

    mitigation = penetration_depth >= band.height / Decimal("2") and _closes_on_polarity_side(
        candle,
        band,
        polarity,
    )

    if rejection:
        interactions.append(
            ZoneInteraction(
                kind=InteractionKind.REJECTION,
                candle_index=candle_index,
                observed_at=candle.close_time,
                penetration_depth=penetration_depth,
                close_price=candle.close,
                rejection_wick=rejection_wick,
                close_through=False,
            )
        )

    if mitigation:
        interactions.append(
            ZoneInteraction(
                kind=InteractionKind.MITIGATION,
                candle_index=candle_index,
                observed_at=candle.close_time,
                penetration_depth=penetration_depth,
                close_price=candle.close,
                rejection_wick=rejection_wick,
                close_through=False,
            )
        )

    if rejection or mitigation:
        interactions.append(
            ZoneInteraction(
                kind=InteractionKind.RESPECT,
                candle_index=candle_index,
                observed_at=candle.close_time,
                penetration_depth=penetration_depth,
                close_price=candle.close,
                rejection_wick=rejection_wick,
                close_through=False,
            )
        )

    return tuple(interactions)


def _touches(
    candle: Candle,
    band: ZoneBand,
) -> bool:
    return candle.high >= band.low and candle.low <= band.high


def _violates(
    candle: Candle,
    band: ZoneBand,
    polarity: ZonePolarity,
) -> bool:
    if polarity is ZonePolarity.BULLISH:
        return candle.close < band.low

    return candle.close > band.high


def _closes_on_polarity_side(
    candle: Candle,
    band: ZoneBand,
    polarity: ZonePolarity,
) -> bool:
    if polarity is ZonePolarity.BULLISH:
        return candle.close >= band.high

    return candle.close <= band.low


def _penetration_depth(
    candle: Candle,
    band: ZoneBand,
    polarity: ZonePolarity,
) -> Decimal:
    depth = band.high - candle.low if polarity is ZonePolarity.BULLISH else candle.high - band.low

    return max(
        Decimal("0"),
        min(
            band.height,
            depth,
        ),
    )


def _rejection_wick(
    candle: Candle,
    band: ZoneBand,
    polarity: ZonePolarity,
) -> Decimal:
    if polarity is ZonePolarity.BULLISH:
        wick = min(
            candle.close,
            band.high,
        ) - max(
            candle.low,
            band.low,
        )
    else:
        wick = min(
            candle.high,
            band.high,
        ) - max(
            candle.close,
            band.low,
        )

    return max(
        Decimal("0"),
        wick,
    )
