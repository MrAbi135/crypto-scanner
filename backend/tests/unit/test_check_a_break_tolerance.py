"""Check A's break test must apply SLS §3.5's `ε`, and must not without it.

`ops/soak/break_tolerance.py` exists because check A asked `close < lo` flat.
§3.5 edge case (2) says a break of a level within `ε` is not a break, so the
flat form demanded an event the doctrine forbids -- BNBUSDT M15 on 2026-09-24,
penetration 0.06 against `ε` 0.0976, flagged for two hourly runs.

These drive `_verdict` itself rather than asserting on a shape passed to it.
The first draft of the notifier's tests asserted against an internal helper and
would have passed against the defect they were written for; the lesson stuck.

**The series.** The flat-ATR recipe: bars shaped `o = p-0.25, c = p+0.25,
h = p+2, l = p-2` with `p` stepping by 1. True range is then `h - l = 4` on
every candle -- the close-gap terms are at most 3.25 -- so Wilder returns
exactly `4` from `ATR_PERIOD - 1` onwards and `ε = TOLERANCE_ATR x 4 = 0.20`
is a fixed number rather than something the test has to discover. A test that
computed the expected `ε` the same way the code does would agree with any
value the code produced, including a wrong one.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from scanner.domain.common.atr import ATR_PERIOD, wilder_atr_series
from scanner.domain.structure import SwingKind, detect_external_swings
from scanner.shared import Timeframe
from tests.support.builders import make_candle

REPO = Path(__file__).resolve().parents[3]

HELPER = REPO / "ops" / "soak" / "break_tolerance.py"


def _module():
    """Import the helper by path -- `ops/` is not a package on sys.path."""
    spec = importlib.util.spec_from_file_location("break_tolerance", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


# `p` walks down to 106 and back up, then down again to sit on the level, so a
# single swing low confirms with five strictly higher lows on each side.
LEVELS = (
    [112, 111, 110, 109, 108, 107, 106, 107, 108, 109, 110, 111, 112]
    + [111, 110, 109, 108, 107, 106, 105]
)

SWING_INDEX = 6

LEVEL_PRICE = Decimal("104")  # low of the p=106 candle

EPSILON = Decimal("0.20")  # TOLERANCE_ATR 0.05 x ATR 4


def _series(final_close: Decimal | None) -> list:
    """The recipe, plus one trailing candle whose close is the thing under test."""
    base = Timeframe.H1
    start = make_candle(timeframe=base).open_time

    out = []

    for index, level in enumerate(LEVELS):
        p = Decimal(level)
        out.append(
            make_candle(
                symbol="TOLUSDT",
                timeframe=base,
                open_time=start + base.duration * index,
                open_=p - Decimal("0.25"),
                close=p + Decimal("0.25"),
                high=p + Decimal("2"),
                low=p - Decimal("2"),
            )
        )

    if final_close is not None:
        p = Decimal("104")
        index = len(LEVELS)
        out.append(
            make_candle(
                symbol="TOLUSDT",
                timeframe=base,
                open_time=start + base.duration * index,
                open_=p - Decimal("0.25"),
                close=final_close,
                high=p + Decimal("2"),
                low=p - Decimal("2"),
            )
        )

    return out


def test_the_series_is_the_one_the_assertions_assume() -> None:
    """Assert the premise before the claim -- the BPR fixture lesson."""
    series = _series(Decimal("103.85"))

    assert len(series) > ATR_PERIOD

    atrs = wilder_atr_series(series)

    assert atrs[-1] == Decimal("4"), (
        f"flat-ATR recipe broken: ATR is {atrs[-1]}, so the 0.20 epsilon these "
        "tests hard-code no longer follows"
    )

    lows = [s for s in detect_external_swings(series) if s.kind is SwingKind.LOW]

    assert [s.index for s in lows] == [SWING_INDEX], (
        f"expected exactly one confirmed external swing low at {SWING_INDEX}, "
        f"got {[(s.index, str(s.price)) for s in lows]}"
    )

    assert lows[0].price == LEVEL_PRICE


def test_a_penetration_inside_epsilon_is_not_a_break() -> None:
    """0.15 below the level, against an epsilon of 0.20."""
    module = _module()

    pen, eps, broke = module._verdict(_series(Decimal("103.85")), "BEARISH")

    assert pen == Decimal("0.15")
    assert eps == EPSILON
    assert broke is False, (
        "a penetration inside epsilon is not a break (SLS §3.5 edge case 2), "
        "so check A must not demand a BOS for it"
    )


def test_a_penetration_beyond_epsilon_is_a_break() -> None:
    """0.30 below the level clears the same epsilon."""
    module = _module()

    pen, eps, broke = module._verdict(_series(Decimal("103.70")), "BEARISH")

    assert pen == Decimal("0.30")
    assert eps == EPSILON
    assert broke is True, (
        "a decisive penetration is still a break -- the tolerance must not "
        "silence the defect check A exists to catch"
    )


def test_it_is_the_epsilon_that_makes_the_difference(monkeypatch) -> None:
    """The whole change, isolated: with the tolerance gone, 0.15 flips to a break.

    This is the test that fails against check A as it was. Without it, both
    assertions above would also pass on a helper that returned `broke` from a
    plain `close < level` comparison for some unrelated reason.
    """
    module = _module()

    inside = _series(Decimal("103.85"))

    assert module._verdict(inside, "BEARISH")[2] is False

    monkeypatch.setattr(module, "TOLERANCE_ATR", Decimal("0"))

    pen, eps, broke = module._verdict(inside, "BEARISH")

    assert eps == Decimal("0")
    assert pen == Decimal("0.15")
    assert broke is True, (
        "with epsilon forced to zero the same close must read as a break -- if "
        "it does not, these tests are not measuring the tolerance at all"
    )


@pytest.mark.parametrize("trend", ["RANGING", "", "NONSENSE"])
def test_a_state_with_no_open_gate_yields_no_verdict(trend: str) -> None:
    """§3.5 records breaks only with the trend, so there is no gate to test."""
    module = _module()

    assert module._verdict(_series(Decimal("103.70")), trend) == (
        Decimal(0),
        Decimal(0),
        False,
    )


def test_a_close_that_does_not_reach_the_level_is_not_a_penetration() -> None:
    module = _module()

    pen, _, broke = module._verdict(_series(Decimal("104.25")), "BEARISH")

    assert pen == Decimal(0)
    assert broke is False
