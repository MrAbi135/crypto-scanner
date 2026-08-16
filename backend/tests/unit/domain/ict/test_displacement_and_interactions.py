"""Tests for displacement and shared zone interaction grammar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.domain.ict import (
    DisplacementDirection,
    InteractionKind,
    ZoneBand,
    ZonePolarity,
    detect_displacement,
    evaluate_zone_interaction,
)
from scanner.shared import Timeframe


def make_candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    base = datetime(
        2026,
        8,
        16,
        0,
        0,
        tzinfo=UTC,
    )

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=base + timedelta(minutes=index * 5),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.REBUILT,
    )


def test_bullish_displacement_requires_full_warmup() -> None:
    candles = [
        make_candle(
            index,
            open_="100",
            high="102",
            low="99",
            close="101",
        )
        for index in range(20)
    ]

    candles.append(
        make_candle(
            20,
            open_="100",
            high="109",
            low="99",
            close="108",
        )
    )

    event = detect_displacement(
        candles,
        20,
        atr=Decimal("5"),
    )

    assert event is not None
    assert event.direction is DisplacementDirection.BULLISH
    assert event.body == Decimal("8")
    assert event.body_multiple == Decimal("8")
    assert event.range_multiple == Decimal("2")
    assert event.close_position == Decimal("0.1")


def test_displacement_undefined_before_20_candle_warmup() -> None:
    candles = [
        make_candle(
            index,
            open_="100",
            high="104",
            low="99",
            close="103",
        )
        for index in range(10)
    ]

    assert (
        detect_displacement(
            candles,
            9,
            atr=Decimal("2"),
        )
        is None
    )


def test_doji_cannot_be_displacement() -> None:
    candles = [
        make_candle(
            index,
            open_="100",
            high="102",
            low="99",
            close="101",
        )
        for index in range(20)
    ]

    candles.append(
        make_candle(
            20,
            open_="100",
            high="110",
            low="90",
            close="100",
        )
    )

    assert (
        detect_displacement(
            candles,
            20,
            atr=Decimal("5"),
        )
        is None
    )


def test_zone_rejection_and_respect_are_emitted_together() -> None:
    candle = make_candle(
        1,
        open_="112",
        high="113",
        low="106",
        close="111",
    )

    interactions = evaluate_zone_interaction(
        candle,
        candle_index=1,
        band=ZoneBand(
            low=Decimal("100"),
            high=Decimal("110"),
        ),
        polarity=ZonePolarity.BULLISH,
        atr=Decimal("10"),
    )

    kinds = {interaction.kind for interaction in interactions}

    assert InteractionKind.TOUCH in kinds
    assert InteractionKind.REJECTION in kinds
    assert InteractionKind.RESPECT in kinds
    assert InteractionKind.VIOLATION not in kinds


def test_zone_mitigation_requires_half_depth_and_respect_close() -> None:
    candle = make_candle(
        1,
        open_="112",
        high="113",
        low="104",
        close="111",
    )

    interactions = evaluate_zone_interaction(
        candle,
        candle_index=1,
        band=ZoneBand(
            low=Decimal("100"),
            high=Decimal("110"),
        ),
        polarity=ZonePolarity.BULLISH,
        atr=Decimal("10"),
    )

    kinds = {interaction.kind for interaction in interactions}

    assert InteractionKind.MITIGATION in kinds
    assert InteractionKind.RESPECT in kinds


def test_close_through_is_violation_not_respect() -> None:
    candle = make_candle(
        1,
        open_="105",
        high="108",
        low="95",
        close="99",
    )

    interactions = evaluate_zone_interaction(
        candle,
        candle_index=1,
        band=ZoneBand(
            low=Decimal("100"),
            high=Decimal("110"),
        ),
        polarity=ZonePolarity.BULLISH,
        atr=Decimal("10"),
    )

    kinds = {interaction.kind for interaction in interactions}

    assert InteractionKind.TOUCH in kinds
    assert InteractionKind.VIOLATION in kinds
    assert InteractionKind.RESPECT not in kinds
