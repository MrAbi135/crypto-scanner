"""Per-minute aggTrade aggregates (SLS §2.2, DDD T4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.common import (
    TradePrint,
    aggregate_minute,
    aggregate_prints,
    minute_of,
    percentile,
)

BASE = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)


def _print(second: int, size: str, *, buyer: bool = True) -> TradePrint:
    return TradePrint(
        at=BASE + timedelta(seconds=second),
        size=Decimal(size),
        taker_is_buyer=buyer,
    )


def test_a_print_must_have_a_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        TradePrint(at=BASE, size=Decimal(0), taker_is_buyer=True)


def test_the_bucket_is_the_minute_the_print_landed_in() -> None:
    assert minute_of(BASE + timedelta(seconds=59, microseconds=999)) == BASE


def test_an_empty_minute_is_no_row_rather_than_a_zero_one() -> None:
    """A trade count of zero divides by itself in every mean downstream."""
    assert aggregate_minute("BTCUSDT", BASE, []) is None


def test_the_taker_side_is_split_not_netted() -> None:
    """§2.2's whole reason for keeping aggTrades is the taker side."""
    aggregate = aggregate_minute(
        "BTCUSDT",
        BASE,
        [_print(0, "3"), _print(1, "1", buyer=False), _print(2, "2")],
    )

    assert aggregate is not None
    assert aggregate.taker_buy_volume == Decimal(5)
    assert aggregate.taker_sell_volume == Decimal(1)
    assert aggregate.trade_count == 3


def test_the_size_distribution_survives_the_fold() -> None:
    """The prints are discarded, so whatever §6.5 and §6.6 need must be here.

    Sizes 1..10: mean 5.5, population stddev sqrt(8.25), p90 the ninth value
    by nearest rank, max the tenth.
    """
    aggregate = aggregate_minute(
        "BTCUSDT",
        BASE,
        [_print(i, str(i + 1)) for i in range(10)],
    )

    assert aggregate is not None
    assert aggregate.mean_trade_size == Decimal("5.5")
    assert aggregate.p90_trade_size == Decimal(9)
    assert aggregate.max_trade_size == Decimal(10)
    assert aggregate.stddev_trade_size == Decimal("8.25").sqrt().quantize(
        aggregate.stddev_trade_size
    )


def test_a_uniform_minute_has_no_dispersion() -> None:
    """§6.6's wash signature is exactly this: every print the same size."""
    aggregate = aggregate_minute("BTCUSDT", BASE, [_print(i, "4") for i in range(5)])

    assert aggregate is not None
    assert aggregate.stddev_trade_size == 0
    assert aggregate.mean_trade_size == Decimal(4)


def test_the_percentile_is_a_size_somebody_printed() -> None:
    """Nearest rank, not interpolation: an interpolated percentile invents a
    trade size that never happened."""
    sizes = [Decimal(n) for n in (1, 2, 3, 4, 5)]

    assert percentile(sizes, Decimal("0.90")) == Decimal(5)
    assert percentile(sizes, Decimal("0.50")) == Decimal(3)
    assert percentile([Decimal(7)], Decimal("0.90")) == Decimal(7)


def test_an_empty_percentile_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="undefined"):
        percentile([], Decimal("0.90"))


def test_prints_are_folded_into_their_own_minutes_oldest_first() -> None:
    aggregates = aggregate_prints(
        "BTCUSDT",
        [
            _print(90, "2"),
            _print(5, "1"),
            _print(150, "3"),
        ],
    )

    assert [item.minute for item in aggregates] == [
        BASE,
        BASE + timedelta(minutes=1),
        BASE + timedelta(minutes=2),
    ]
    assert [item.trade_count for item in aggregates] == [1, 1, 1]
