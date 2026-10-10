"""SLS v1.0.15 §15.2's `risk_stop`, and the single definition of R it forced.

Two things are pinned here, and the second matters more than the first.

**The stop itself** -- a fixed percent of the entry's proximal edge, against
D, whatever the archetype.

**One R.** Before this change R was computed in three places: `r_unit`, the
payload's invalidation distance, and §12.4's outcome accounting -- the last two
reading `entry.mid` directly. A change of anchor applied in one of them would
have left the other two in the old unit and produced no error at all: just
excursions and distances that disagree with the R-multiple printed beside
them. So the tests below assert agreement across all three rather than the
value of any one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    RISK_STOP,
    RISK_STOP_PCT,
    ZONE_DISTAL_EDGE,
    Archetype,
    SignalLevels,
    TargetBand,
    entry_zone,
    invalidation_for,
    risk_stop_for,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import Candle, SignalState, accounting

ONE_PCT = Decimal("1.0")


def zone(direction: str = "UP", low: str = "100", high: str = "104"):
    """A 4%-wide zone: proximal 104 for a long, 100 for a short; mid 102."""
    return entry_zone(
        zone_id="z1",
        direction=direction,
        band_low=Decimal(low),
        band_high=Decimal(high),
    )


def levels(entry, invalidation: Invalidation, *, direction: str = "UP", target: str = "120"):
    return SignalLevels(
        direction=direction,
        entry=entry,
        invalidation=invalidation,
        primary_target=TargetBand(low=Decimal(target), high=Decimal(target)),
    )


# --- the stop ----------------------------------------------------------------


def test_a_long_risk_stop_sits_the_percent_below_the_proximal_edge() -> None:
    stop = risk_stop_for(entry=zone("UP"), direction="UP", stop_pct=ONE_PCT)

    assert stop.rule == RISK_STOP
    assert stop.price == Decimal("102.96"), "1% below a proximal edge of 104"


def test_a_short_risk_stop_sits_the_percent_above_the_proximal_edge() -> None:
    stop = risk_stop_for(entry=zone("DOWN"), direction="DOWN", stop_pct=ONE_PCT)

    assert stop.rule == RISK_STOP
    assert stop.price == Decimal("101.00"), "1% above a proximal edge of 100"


def test_the_stop_is_measured_from_the_proximal_edge_not_the_mid() -> None:
    """The mid of this zone is 102; 1% of it would be 100.98, not 102.96."""
    stop = risk_stop_for(entry=zone("UP"), direction="UP", stop_pct=ONE_PCT)

    assert stop.price != Decimal(102) * Decimal("0.99")


@pytest.mark.parametrize("bad", [Decimal(0), Decimal("-1")])
def test_a_non_positive_percent_is_refused(bad: Decimal) -> None:
    """Zero is the zero-R case; a negative one would sit on the wrong side of
    the entry and pass every check that only measures distance."""
    with pytest.raises(ValueError):
        risk_stop_for(entry=zone("UP"), direction="UP", stop_pct=bad)


def test_an_unknown_direction_is_refused() -> None:
    with pytest.raises(ValueError):
        risk_stop_for(entry=zone("UP"), direction="SIDEWAYS", stop_pct=ONE_PCT)


def test_the_mapping_ships_empty_until_the_m5_change() -> None:
    """A tripwire, and the change that flips M5 on edits it on purpose.

    Measured on the 14 published M5 signals: the 1% stop alone is not robust
    (+1.68%, negative once the best two trades are dropped) and the ladder
    alone is worse than the rule it replaces (-4.68%). Only the two together
    survive. Populating this mapping without the ladder and TTL 48 ships the
    configuration that measurement rejected -- so it must happen in the same
    change, and this test is what makes that a conscious edit rather than a
    one-line slip.
    """
    assert RISK_STOP_PCT == {}


# --- one anchor ---------------------------------------------------------------


def test_a_risk_stop_anchors_r_at_the_proximal_edge() -> None:
    entry = zone("UP")
    lv = levels(entry, risk_stop_for(entry=entry, direction="UP", stop_pct=ONE_PCT))

    assert lv.anchor == entry.proximal == Decimal(104)
    assert lv.r_unit == Decimal("1.04"), "R is exactly 1% of the proximal price"


def test_every_other_rule_still_anchors_r_at_the_mid() -> None:
    """§12.4 as written, for every rule that predates v1.0.15."""
    entry = zone("UP")
    lv = levels(
        entry, invalidation_for(Archetype.FVG_CONTINUATION, entry=entry, swept_extreme=None)
    )

    assert lv.invalidation.rule == ZONE_DISTAL_EDGE
    assert lv.anchor == entry.mid == Decimal(102)
    assert lv.r_unit == Decimal(2)


def test_an_empty_rule_falls_back_to_the_mid() -> None:
    """What a pre-v1.0.15 payload with no recorded rule reconstructs to."""
    entry = zone("UP")
    lv = levels(entry, Invalidation(Decimal(98), ""))

    assert lv.anchor == entry.mid


def test_the_r_multiple_reaches_from_the_same_anchor_r_is_measured_from() -> None:
    entry = zone("UP")
    lv = levels(entry, risk_stop_for(entry=entry, direction="UP", stop_pct=ONE_PCT))

    # (120 - 104) / 1.04 -- both distances from the proximal edge.
    assert lv.r_multiple == Decimal(16) / Decimal("1.04")


def test_a_risk_stop_inside_a_wide_zone_is_still_coherent() -> None:
    """The trap a mid-based coherence check would spring.

    A 10%-wide zone has its mid at 105 and its proximal edge at 110, so a 1%
    risk stop lands at 108.9 -- ABOVE the mid. Judged from the mid, stop and
    entry are on the wrong sides and §15.3(1) refuses the signal as
    INCOHERENT_LEVELS. Judged from where the fill happens, they are in perfect
    order. Every zone wider than 2% would have been refused this way.
    """
    entry = zone("UP", low="100", high="110")
    stop = risk_stop_for(entry=entry, direction="UP", stop_pct=ONE_PCT)
    lv = levels(entry, stop, target="130")

    assert stop.price > entry.mid, "the premise: the stop sits above the mid"
    assert lv.coherent, "a correctly ordered risk stop was refused as incoherent"


# --- §12.4 reads the same R ---------------------------------------------------


def test_outcome_excursions_use_the_levels_own_r_and_anchor() -> None:
    """§12.4 used to compute R and its origin from `entry.mid` on its own.

    With a risk stop that would have reported MFE from 102 in units of 0.96
    -- a number in no unit anything else uses. It must come out as
    (108 - 104) / 1.04 instead: the levels' anchor, the levels' R.
    """
    entry = zone("UP")
    lv = levels(entry, risk_stop_for(entry=entry, direction="UP", stop_pct=ONE_PCT))

    book = accounting(
        SignalState.SUCCESS,
        levels=lv,
        candles=[Candle(high=Decimal(108), low=Decimal(103), close=Decimal(107))],
    )

    assert book.mfe_r == (Decimal(108) - lv.anchor) / lv.r_unit
    assert book.mfe_r == Decimal(4) / Decimal("1.04")
    assert book.mae_r == (lv.anchor - Decimal(103)) / lv.r_unit


def test_outcome_on_a_zone_rule_is_unchanged() -> None:
    """The neutrality half: every signal published so far keeps its numbers."""
    entry = zone("UP")
    lv = levels(entry, Invalidation(Decimal(98), ZONE_DISTAL_EDGE))

    book = accounting(
        SignalState.SUCCESS,
        levels=lv,
        candles=[Candle(high=Decimal(108), low=Decimal(100), close=Decimal(107))],
    )

    assert book.mfe_r == Decimal(6) / Decimal(4), "from the mid 102, R = |102 - 98|"
    assert book.mae_r == Decimal(2) / Decimal(4)
