"""SLS §1.6's automatic stablecoin classifier.

The fixtures are shaped on what the classifier's first run over live Binance
data measured on 2026-09-17: every USD stablecoin sat between 0.02% and 0.17%
from 1.00, and the quiet non-pegs that a spread-around-the-mean reading would
also have flagged sat far from 1.00 (EUR 16%, a tokenized ETF 71,000%).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.common import (
    PEG_DEVIATION_LIMIT,
    PEG_WINDOW_DAYS,
    StableFlag,
    next_stable_flag,
    peg_deviation,
)


def closes(*values: str) -> list[Decimal]:
    """Thirty closes cycling through `values`."""
    return [Decimal(values[i % len(values)]) for i in range(PEG_WINDOW_DAYS)]


def test_a_dollar_peg_measures_as_its_distance_from_one() -> None:
    # USDC-like: +/- 0.03% around 1.00 -> RMS 0.0003.
    assert peg_deviation(closes("1.0003", "0.9997")) == Decimal("0.0003")


def test_the_measure_is_distance_from_one_not_stillness() -> None:
    """EURUSDT's 30 closes spread 0.49% around their own mean -- quiet enough
    to pass a spread test -- but sit 16% from a dollar."""
    euro = closes("1.1479", "1.1480")

    deviation = peg_deviation(euro)

    assert deviation is not None
    assert deviation > Decimal("0.14")
    assert next_stable_flag(None, deviation) is None


def test_a_flat_price_far_from_one_is_not_a_peg() -> None:
    assert peg_deviation(closes("0.011387")) == Decimal("0.988613")


@pytest.mark.parametrize("days", [0, 7, 29, 31])
def test_only_exactly_thirty_closes_measure_anything(days: int) -> None:
    """A week-old listing has no 30-day record, and the window is the rule."""
    assert peg_deviation([Decimal("1")] * days) is None


def test_the_limit_is_strict() -> None:
    at_limit = closes("1.01", "0.99")

    assert peg_deviation(at_limit) == PEG_DEVIATION_LIMIT
    assert next_stable_flag(None, PEG_DEVIATION_LIMIT) is None


def test_a_peg_raises_the_flag() -> None:
    assert next_stable_flag(None, Decimal("0.0004")) is StableFlag.FLAGGED


def test_a_flag_stays_while_the_peg_holds() -> None:
    assert next_stable_flag(StableFlag.FLAGGED, Decimal("0.0004")) is StableFlag.FLAGGED


def test_leaving_the_peg_clears_the_flag() -> None:
    assert next_stable_flag(StableFlag.FLAGGED, Decimal("0.05")) is None


@pytest.mark.parametrize("deviation", [Decimal("0.0001"), Decimal("0.5"), None])
def test_a_dismissal_is_never_overridden_by_a_measurement(deviation: Decimal | None) -> None:
    """A person ruled it is not a stablecoin; re-flagging it nightly would
    make the review meaningless."""
    assert next_stable_flag(StableFlag.DISMISSED, deviation) is StableFlag.DISMISSED


@pytest.mark.parametrize("current", [None, StableFlag.FLAGGED])
def test_no_measurement_changes_nothing(current: StableFlag | None) -> None:
    assert next_stable_flag(current, None) is current
