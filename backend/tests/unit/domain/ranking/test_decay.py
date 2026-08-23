"""§9.3's display-rank decay across §12.5's TTL."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.ranking import TTL_CANDLES, display_rank, ttl_candles
from scanner.shared import Timeframe


def test_the_ttl_table_is_12_5s() -> None:
    """§12.5: M5 24, M15 24, H1 24, H4 18, D1 15."""
    assert TTL_CANDLES == {
        Timeframe.M5: 24,
        Timeframe.M15: 24,
        Timeframe.H1: 24,
        Timeframe.H4: 18,
        Timeframe.D1: 15,
    }


def test_a_fresh_signal_displays_at_its_confidence() -> None:
    assert display_rank(Decimal(90), elapsed_candles=0, timeframe=Timeframe.H1) == Decimal(90)


def test_it_decays_linearly_to_exactly_zero_at_the_ttl() -> None:
    """§9.3: `display_rank = FinalConfidence x remaining_ttl / ttl`."""
    # H4's TTL is 18, so half of it is 9.
    assert display_rank(Decimal(80), elapsed_candles=9, timeframe=Timeframe.H4) == Decimal(40)

    assert display_rank(Decimal(80), elapsed_candles=18, timeframe=Timeframe.H4) == Decimal(0)


def test_past_the_ttl_it_stays_at_zero_rather_than_going_negative() -> None:
    """A negative display rank would sort a dead setup below a live worse one.

    §12.5 expires the signal at the TTL, so nothing should be asking -- but
    the arithmetic is the presentation layer's and it should not produce an
    ordering nobody asked for if it is asked late.
    """
    assert display_rank(Decimal(80), elapsed_candles=40, timeframe=Timeframe.H4) == Decimal(0)


def test_the_recorded_confidence_is_not_what_decays() -> None:
    """§9.3: "display rank (not its recorded confidence)".

    The same confidence read at two ages gives two display ranks and remains
    one number. Obvious of a pure function, and the reason this is a pure
    function.
    """
    confidence = Decimal(90)

    early = display_rank(confidence, elapsed_candles=0, timeframe=Timeframe.D1)
    late = display_rank(confidence, elapsed_candles=10, timeframe=Timeframe.D1)

    assert early == Decimal(90)
    assert late == Decimal(30)
    assert confidence == Decimal(90)


def test_a_timeframe_12_5_does_not_cover_raises_rather_than_assuming() -> None:
    """W1 is a scanned timeframe with no TTL in §12.5.

    The table lists M5 through D1 and stops. Defaulting W1 to D1's 15 would
    decay a weekly setup at the daily rate, and nothing in the output would
    ever say that a number had been invented.
    """
    with pytest.raises(ValueError, match="no TTL for W1"):
        ttl_candles(Timeframe.W1)

    with pytest.raises(ValueError, match="no TTL for W1"):
        display_rank(Decimal(90), elapsed_candles=1, timeframe=Timeframe.W1)


def test_negative_elapsed_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        display_rank(Decimal(90), elapsed_candles=-1, timeframe=Timeframe.H1)
