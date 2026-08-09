"""Liquidity metric calculations (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scanner.application.ports.liquidity_provider import (
    OrderBookSnapshot,
    TopOfBook,
)

_TWO_PERCENT = Decimal("0.02")
_BPS = Decimal("10000")
_TWO = Decimal("2")


@dataclass(frozen=True, slots=True)
class LiquidityMetrics:
    """Instantaneous liquidity measurements for one symbol."""

    spread_bps: Decimal
    depth_2pct: Decimal


def calculate_spread_bps(top: TopOfBook) -> Decimal:
    """Calculate mid-price relative bid/ask spread in basis points."""

    if top.bid_price <= 0:
        raise ValueError("bid_price must be positive")

    if top.ask_price <= 0:
        raise ValueError("ask_price must be positive")

    if top.ask_price < top.bid_price:
        raise ValueError("ask_price must be greater than or equal to bid_price")

    midpoint = (top.bid_price + top.ask_price) / _TWO

    return (
        (top.ask_price - top.bid_price)
        / midpoint
        * _BPS
    )


def calculate_depth_2pct(
    top: TopOfBook,
    book: OrderBookSnapshot,
) -> Decimal:
    """Calculate total quote-value depth within ±2% of the midpoint."""

    if book.exchange_symbol != top.exchange_symbol:
        raise ValueError(
            "top-of-book and order-book symbols must match"
        )

    midpoint = (top.bid_price + top.ask_price) / _TWO

    if midpoint <= 0:
        raise ValueError("midpoint must be positive")

    lower_bound = midpoint * (
        Decimal("1") - _TWO_PERCENT
    )
    upper_bound = midpoint * (
        Decimal("1") + _TWO_PERCENT
    )

    bid_depth = sum(
        (
            level.price * level.quantity
            for level in book.bids
            if lower_bound <= level.price <= midpoint
        ),
        Decimal("0"),
    )

    ask_depth = sum(
        (
            level.price * level.quantity
            for level in book.asks
            if midpoint <= level.price <= upper_bound
        ),
        Decimal("0"),
    )

    return bid_depth + ask_depth


def calculate_liquidity_metrics(
    top: TopOfBook,
    book: OrderBookSnapshot,
) -> LiquidityMetrics:
    """Calculate spread and ±2% quote depth from one snapshot pair."""

    return LiquidityMetrics(
        spread_bps=calculate_spread_bps(top),
        depth_2pct=calculate_depth_2pct(
            top,
            book,
        ),
    )
