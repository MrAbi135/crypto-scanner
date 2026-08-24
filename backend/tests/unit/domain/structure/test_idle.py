"""§3.4's second route into RANGING."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.structure import IDLE_CANDLES, structure_is_idle

LOW = Decimal(100)
HIGH = Decimal(110)


def closes(*values: str, length: int = IDLE_CANDLES) -> list[Decimal]:
    """A window of `length` closes ending in `values`."""
    body = [Decimal(v) for v in values]

    return [Decimal(105)] * (length - len(body)) + body


def test_a_quiet_window_inside_the_bracket_is_idle() -> None:
    """§3.4: "closed inside the current external dealing range without
    external BOS for `P.structure.idle_candles = 100` closed candles"."""

    assert structure_is_idle(
        closes(),
        range_low=LOW,
        range_high=HIGH,
        broke_externally=False,
    )


def test_one_close_outside_the_bracket_ends_it() -> None:
    """ "Closed inside" is every close, not most of them.

    A single excursion is the market leaving the range, and a rule that
    tolerated one would tolerate a hundred one at a time.
    """
    window = closes()
    window[7] = HIGH + 1

    assert not structure_is_idle(
        window,
        range_low=LOW,
        range_high=HIGH,
        broke_externally=False,
    )


def test_the_bracket_edges_count_as_inside() -> None:
    """A close exactly at an anchor has not left the range.

    The anchors are swing extremes -- prices the market has already traded --
    so touching one is ordinary behaviour inside a range, not an exit from it.
    """
    window = closes()
    window[3] = LOW
    window[4] = HIGH

    assert structure_is_idle(
        window,
        range_low=LOW,
        range_high=HIGH,
        broke_externally=False,
    )


def test_a_break_in_the_window_ends_it_however_quiet_the_range() -> None:
    """Both conditions, not either.

    A trend still breaking external levels is not idle however narrow its
    range -- and the range condition alone would call it idle, because a break
    that closes back inside the bracket leaves no trace in the closes.
    """
    assert not structure_is_idle(
        closes(),
        range_low=LOW,
        range_high=HIGH,
        broke_externally=True,
    )


def test_too_short_a_series_is_not_idle() -> None:
    """ "Too early to tell" is not "idle".

    Treating a short series as idle would drop every young market into
    RANGING the moment it earned a trend, which is the opposite of what §3.4
    is for.
    """
    assert not structure_is_idle(
        closes(length=IDLE_CANDLES - 1),
        range_low=LOW,
        range_high=HIGH,
        broke_externally=False,
    )


def test_only_the_last_hundred_candles_are_read() -> None:
    """Everything before the window is history, however violent.

    The rule asks what the market has done lately. A series that broke out a
    year ago and has sat still since is idle, and reading further back would
    make the answer depend on how much history happened to be loaded.
    """
    window = [Decimal(1)] * 50 + closes()

    assert structure_is_idle(
        window,
        range_low=LOW,
        range_high=HIGH,
        broke_externally=False,
    )


def test_an_inverted_bracket_is_refused() -> None:
    with pytest.raises(ValueError, match="range_high must be"):
        structure_is_idle(
            closes(),
            range_low=HIGH,
            range_high=LOW,
            broke_externally=False,
        )
