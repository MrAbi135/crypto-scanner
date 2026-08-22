"""Tests for close-confirmed BOS doctrine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.structure import (
    BreakDirection,
    SwingKind,
    SwingPoint,
    SwingStrength,
    detect_bos,
    failed_break_index,
    is_wick_only_penetration,
)
from scanner.shared import Timeframe


def candle(
    *,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        ),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def swing(
    *,
    price: str,
    kind: SwingKind,
) -> SwingPoint:
    return SwingPoint(
        index=10,
        open_time=datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        ),
        price=Decimal(price),
        kind=kind,
        strength=SwingStrength.EXTERNAL,
    )


def test_upward_bos_requires_close_above_swing() -> None:
    event = detect_bos(
        candle(
            high="111",
            low="95",
            close="110.1",
        ),
        swing(
            price="110",
            kind=SwingKind.HIGH,
        ),
        direction=BreakDirection.UP,
    )

    assert event is not None
    assert event.direction is BreakDirection.UP


def test_upward_wick_only_penetration_is_not_bos() -> None:
    source = candle(
        high="112",
        low="95",
        close="109",
    )
    level = swing(
        price="110",
        kind=SwingKind.HIGH,
    )

    assert (
        detect_bos(
            source,
            level,
            direction=BreakDirection.UP,
        )
        is None
    )

    assert is_wick_only_penetration(
        source,
        level,
        direction=BreakDirection.UP,
    )


def test_downward_bos_requires_close_below_swing() -> None:
    event = detect_bos(
        candle(
            high="105",
            low="88",
            close="89",
        ),
        swing(
            price="90",
            kind=SwingKind.LOW,
        ),
        direction=BreakDirection.DOWN,
    )

    assert event is not None
    assert event.direction is BreakDirection.DOWN


def test_downward_wick_only_penetration_is_not_bos() -> None:
    source = candle(
        high="105",
        low="88",
        close="91",
    )
    level = swing(
        price="90",
        kind=SwingKind.LOW,
    )

    assert (
        detect_bos(
            source,
            level,
            direction=BreakDirection.DOWN,
        )
        is None
    )

    assert is_wick_only_penetration(
        source,
        level,
        direction=BreakDirection.DOWN,
    )


def test_bos_respects_epsilon() -> None:
    assert (
        detect_bos(
            candle(
                high="111",
                low="95",
                close="110.05",
            ),
            swing(
                price="110",
                kind=SwingKind.HIGH,
            ),
            direction=BreakDirection.UP,
            epsilon=Decimal("0.1"),
        )
        is None
    )


def test_wrong_swing_kind_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="upward BOS must reference a swing high",
    ):
        detect_bos(
            candle(
                high="111",
                low="90",
                close="111",
            ),
            swing(
                price="100",
                kind=SwingKind.LOW,
            ),
            direction=BreakDirection.UP,
        )


def test_negative_epsilon_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="epsilon must be non-negative",
    ):
        detect_bos(
            candle(
                high="111",
                low="90",
                close="111",
            ),
            swing(
                price="100",
                kind=SwingKind.HIGH,
            ),
            direction=BreakDirection.UP,
            epsilon=Decimal("-1"),
        )


def _series(closes: list[str]) -> list[Candle]:
    """`candle()` fixes open at 100, so the wicks have to bracket it."""
    return [
        candle(
            high=str(max(Decimal(c), Decimal("100"))),
            low=str(min(Decimal(c), Decimal("100"))),
            close=c,
        )
        for c in closes
    ]


def test_a_close_back_through_the_level_is_a_failed_break() -> None:
    """§3.5: "price closes back beyond the broken level in the opposite
    direction" within `failed_break_candles = 3`."""
    candles = _series(["125", "105", "105", "105"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        == 1
    )


def test_a_break_that_holds_is_not_a_failure() -> None:
    candles = _series(["125", "124", "126", "130"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        is None
    )


def test_the_window_closes_after_three_candles() -> None:
    """A reclaim on the fourth candle is a later move, not a failed break."""
    candles = _series(["125", "124", "123", "122", "105"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        is None
    )


def test_the_first_reclaim_is_the_one_recorded() -> None:
    """The break has already failed by then; a deeper close is the same fact."""
    candles = _series(["125", "110", "90"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        == 1
    )


def test_a_close_exactly_on_the_level_has_not_come_back_through() -> None:
    """ "Beyond" is strict: sitting on the level is neither side of it."""
    candles = _series(["125", "120"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        is None
    )


def test_the_bearish_mirror() -> None:
    candles = _series(["75", "95", "95"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("80"), direction=BreakDirection.DOWN
        )
        == 1
    )


def test_a_break_on_the_last_candle_cannot_fail_yet() -> None:
    """Nothing has closed after it, so there is no evidence either way."""
    candles = _series(["125"])

    assert (
        failed_break_index(
            candles, break_index=0, level=Decimal("120"), direction=BreakDirection.UP
        )
        is None
    )
