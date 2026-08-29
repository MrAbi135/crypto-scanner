"""The per-candle epsilon series the touch counter walks with."""

from __future__ import annotations

from decimal import Decimal

from scanner.application.detection.liquidity_replay import _epsilons_for
from scanner.domain.common import TOLERANCE_ATR


def test_each_candle_gets_its_own_atr_derived_epsilon() -> None:
    atrs = [None, Decimal("2"), Decimal("4"), Decimal("0")]

    assert _epsilons_for(atrs, 0, 4) == (
        # Unmeasured is the STRICTEST reading, not a forgiving default: an
        # ATR-less candle must neither manufacture a touch a measured candle
        # would refuse nor forgive a breach. Zero epsilon is that reading.
        Decimal(0),
        TOLERANCE_ATR * Decimal("2"),
        TOLERANCE_ATR * Decimal("4"),
        Decimal(0),
    )


def test_the_slice_is_the_callers_window() -> None:
    atrs = [Decimal("1"), Decimal("2"), Decimal("3")]

    assert _epsilons_for(atrs, 1, 3) == (
        TOLERANCE_ATR * Decimal("2"),
        TOLERANCE_ATR * Decimal("3"),
    )
