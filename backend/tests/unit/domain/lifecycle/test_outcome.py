"""§12.4's MFE/MAE accounting."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    ZONE_DISTAL_EDGE,
    SignalLevels,
    TargetBand,
    entry_zone,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import Candle, SignalState, accounting

# Entry band [100, 104] -> mid 102; invalidation 98 -> R = 4.
LONG = SignalLevels(
    direction="UP",
    entry=entry_zone(zone_id="z", direction="UP", band_low=Decimal(100), band_high=Decimal(104)),
    invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
    primary_target=TargetBand(low=Decimal(112), high=Decimal(114)),
)

SHORT = SignalLevels(
    direction="DOWN",
    entry=entry_zone(zone_id="z", direction="DOWN", band_low=Decimal(100), band_high=Decimal(104)),
    invalidation=Invalidation(Decimal(106), ZONE_DISTAL_EDGE),
    primary_target=TargetBand(low=Decimal(90), high=Decimal(92)),
)


def candle(high: str, low: str, close: str) -> Candle:
    return Candle(high=Decimal(high), low=Decimal(low), close=Decimal(close))


def test_excursions_are_measured_from_the_entry_mid_in_r() -> None:
    """§12.4: "R = |entry mid - invalidation|", and both excursions in R units.

    Measured from the same origin R is, so the two are commensurable.
    Measuring favourable travel from the proximal edge and adverse travel from
    the distal one would quietly widen every winner and narrow every loser by
    the width of the band.
    """
    book = accounting(
        SignalState.SUCCESS,
        levels=LONG,
        candles=[candle("110", "101", "108"), candle("114", "99", "112")],
    )

    # Best high 114 is 12 above the mid = 3R; worst low 99 is 3 below = 0.75R.
    assert book.mfe_r == Decimal(3)
    assert book.mae_r == Decimal("0.75")
    assert book.elapsed_candles == 2


def test_excursions_never_go_negative() -> None:
    """ "The furthest it went the right way" cannot be less than nowhere.

    A long whose every candle traded below the entry has an MFE of zero, not a
    negative number that would subtract from an average.
    """
    book = accounting(
        SignalState.FAILED,
        levels=LONG,
        candles=[candle("101", "97", "98")],
    )

    assert book.mfe_r == Decimal(0)
    assert book.mae_r == Decimal("1.25")


def test_the_short_mirror_swaps_which_side_is_favourable() -> None:
    book = accounting(
        SignalState.SUCCESS,
        levels=SHORT,
        candles=[candle("104", "90", "92")],
    )

    # Mid 102, R = 4. Low 90 is 12 below = 3R favourable; high 104 is 2 above
    # = 0.5R adverse.
    assert book.mfe_r == Decimal(3)
    assert book.mae_r == Decimal("0.5")


def test_expired_outcomes_are_reported_but_excluded_from_the_hit_rate() -> None:
    """§12.4: "expired states are excluded from hit-rate but reported (a
    scanner that times out constantly has a target-selection problem --
    visible, not hidden)"."""

    resolved = accounting(SignalState.SUCCESS, levels=LONG, candles=[candle("110", "101", "108")])
    expired = accounting(
        SignalState.EXPIRED_ACTIVE, levels=LONG, candles=[candle("110", "101", "108")]
    )

    assert resolved.counts_toward_hit_rate
    assert not expired.counts_toward_hit_rate

    # Reported, though: the excursion is still measured.
    assert expired.mfe_r == Decimal(2)


def test_a_signal_with_no_candles_has_no_excursion() -> None:
    book = accounting(SignalState.EXPIRED_UNTOUCHED, levels=LONG, candles=[])

    assert (book.mfe_r, book.mae_r, book.elapsed_candles) == (Decimal(0), Decimal(0), 0)


def test_a_zero_r_is_refused_rather_than_divided_by() -> None:
    """An invalidation on the entry mid would make every excursion infinite.

    T17's own check constraint should have refused the signal at publication;
    this is the second line, and it raises rather than recording a number that
    would poison every statistic derived from it.
    """
    broken = SignalLevels(
        direction="UP",
        entry=entry_zone(
            zone_id="z", direction="UP", band_low=Decimal(100), band_high=Decimal(104)
        ),
        invalidation=Invalidation(Decimal(102), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(112), high=Decimal(114)),
    )

    with pytest.raises(ValueError, match="R is zero"):
        accounting(SignalState.FAILED, levels=broken, candles=[candle("110", "101", "108")])
