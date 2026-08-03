"""Candle intrinsic invariants (SLS §2.15 single-candle checks)."""

from datetime import UTC, datetime

import pytest

from scanner.shared import DomainInvariantError, Timeframe, dec
from tests.support.builders import make_candle


def test_valid_candle_constructs() -> None:
    candle = make_candle()
    assert candle.close_time == candle.open_time + Timeframe.H1.duration


def test_ohlc_ordering_enforced() -> None:
    with pytest.raises(DomainInvariantError, match="OHLC ordering"):
        make_candle(open_=dec("100"), close=dec("101"), high=dec("100.5"))  # high < close


def test_high_low_inversion_rejected() -> None:
    with pytest.raises(DomainInvariantError):
        make_candle(high=dec("99"), low=dec("100"), open_=dec("99.5"), close=dec("99.5"))


def test_taker_volume_bounded_by_volume() -> None:
    with pytest.raises(DomainInvariantError, match="taker_buy_volume"):
        make_candle(volume=dec("10"), taker_buy_volume=dec("11"))


def test_misaligned_open_time_rejected() -> None:
    with pytest.raises(DomainInvariantError, match="boundary"):
        make_candle(open_time=datetime(2024, 1, 1, 0, 30, tzinfo=UTC))  # not an H1 boundary


def test_negative_volume_rejected() -> None:
    with pytest.raises(DomainInvariantError):
        make_candle(volume=dec("-1"), taker_buy_volume=dec("-1"))
