"""Unit tests for Sprint S3 liquidity calculations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.application.marketdata.liquidity import (
    calculate_depth_2pct,
    calculate_liquidity_metrics,
    calculate_spread_bps,
)
from scanner.application.ports.liquidity_provider import (
    OrderBookLevel,
    OrderBookSnapshot,
    TopOfBook,
)


def top_of_book(
    *,
    bid: str = "99",
    ask: str = "101",
) -> TopOfBook:
    return TopOfBook(
        exchange_symbol="BTCUSDT",
        bid_price=Decimal(bid),
        bid_quantity=Decimal("1"),
        ask_price=Decimal(ask),
        ask_quantity=Decimal("1"),
    )


def order_book() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange_symbol="BTCUSDT",
        bids=(
            OrderBookLevel(
                price=Decimal("100"),
                quantity=Decimal("2"),
            ),
            OrderBookLevel(
                price=Decimal("99"),
                quantity=Decimal("3"),
            ),
            OrderBookLevel(
                price=Decimal("97"),
                quantity=Decimal("10"),
            ),
        ),
        asks=(
            OrderBookLevel(
                price=Decimal("100"),
                quantity=Decimal("4"),
            ),
            OrderBookLevel(
                price=Decimal("101"),
                quantity=Decimal("5"),
            ),
            OrderBookLevel(
                price=Decimal("103"),
                quantity=Decimal("10"),
            ),
        ),
    )


def test_spread_bps_uses_midpoint() -> None:
    top = top_of_book(
        bid="99",
        ask="101",
    )

    result = calculate_spread_bps(top)

    assert result == Decimal("200")


def test_zero_spread_is_zero_bps() -> None:
    top = top_of_book(
        bid="100",
        ask="100",
    )

    assert calculate_spread_bps(top) == Decimal("0")


def test_non_positive_bid_is_rejected() -> None:
    top = top_of_book(
        bid="0",
        ask="100",
    )

    with pytest.raises(
        ValueError,
        match="bid_price must be positive",
    ):
        calculate_spread_bps(top)


def test_non_positive_ask_is_rejected() -> None:
    top = top_of_book(
        bid="100",
        ask="0",
    )

    with pytest.raises(
        ValueError,
        match="ask_price must be positive",
    ):
        calculate_spread_bps(top)


def test_crossed_book_is_rejected() -> None:
    top = top_of_book(
        bid="101",
        ask="100",
    )

    with pytest.raises(
        ValueError,
        match="ask_price must be greater than or equal to bid_price",
    ):
        calculate_spread_bps(top)


def test_depth_counts_only_levels_within_two_percent() -> None:
    top = top_of_book(
        bid="99",
        ask="101",
    )

    result = calculate_depth_2pct(
        top,
        order_book(),
    )

    expected = (
        Decimal("100") * Decimal("2")
        + Decimal("99") * Decimal("3")
        + Decimal("100") * Decimal("4")
        + Decimal("101") * Decimal("5")
    )

    assert result == expected


def test_depth_rejects_symbol_mismatch() -> None:
    top = top_of_book()

    book = OrderBookSnapshot(
        exchange_symbol="ETHUSDT",
        bids=(),
        asks=(),
    )

    with pytest.raises(
        ValueError,
        match="symbols must match",
    ):
        calculate_depth_2pct(
            top,
            book,
        )


def test_liquidity_metrics_returns_spread_and_depth() -> None:
    top = top_of_book(
        bid="99",
        ask="101",
    )
    book = order_book()

    metrics = calculate_liquidity_metrics(
        top,
        book,
    )

    assert metrics.spread_bps == Decimal("200")
    assert metrics.depth_2pct == Decimal("1402")
