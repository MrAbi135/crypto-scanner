"""`ops/soak/signal_outcomes.py` -- the arithmetic that decides a trade's result.

This scores money, not events, so the failure modes are quiet ones: a unit
mixed up, a boundary resolved the flattering way, unresolved trades dropped.
Each of those produces a plausible table rather than an error, and the
2026-10-10 analysis this tool came out of hit two of them before they were
caught by hand.

What is pinned here is therefore the parts a wrong answer would travel
through: the R unit, the same-candle boundary, the entry requirement, the
entry candle that only activates (as the engine's `observe()` does), the
mark-to-close of unresolved trades, and the control gate that stops the whole
report when the simulator and the engine stop agreeing about what an outcome
is.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

HELPER = REPO / "ops" / "soak" / "signal_outcomes.py"


@pytest.fixture()
def mod():
    """Import by path -- `ops/` is not a package on sys.path."""
    spec = importlib.util.spec_from_file_location("signal_outcomes", HELPER)

    assert spec is not None and spec.loader is not None, f"cannot load {HELPER}"

    module = importlib.util.module_from_spec(spec)
    sys.modules["signal_outcomes"] = module
    spec.loader.exec_module(module)

    yield module

    del sys.modules["signal_outcomes"]


def _signal(
    mod,
    direction: str = "UP",
    *,
    entry="100",
    inval="99",
    target="104",
    ttl=24,
    label="FAILED",
    signal_id="SIG",
):
    """Entry 100, stop 1.0 away, published target 4R away -- the live shape.

    The real signals sit near this: a stop around 0.5% of price and a target
    about 4R beyond it (measured 2026-10-10), so a fixture built on a 1R target
    would exercise geometry production does not produce.
    """
    return mod.Signal(
        signal_id=signal_id,
        symbol="BTCUSDT",
        timeframe="M15",
        direction=direction,
        grade="B",
        entry=Decimal(entry),
        invalidation=Decimal(inval),
        target=Decimal(target),
        ttl_candles=ttl,
        engine_label=label,
    )


def _bar(mod, high, low, close):
    return mod.Bar(high=Decimal(high), low=Decimal(low), close=Decimal(close))


# --- the R unit ----------------------------------------------------------


def test_a_loss_is_charged_in_original_stops_not_in_the_widened_one(mod) -> None:
    """The single most important property in the file.

    A wider stop that charged itself 1R per loss would look free: its losses
    would shrink in its own units while its wins kept the old target. Every
    configuration has to be priced in the same unit or the comparison between
    them is meaningless.
    """
    sig = _signal(mod)
    # Entry touched on bar 1; bar 2 closes at 97, below a 2x stop (98) as well
    # as a 1x stop (99).
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "100", "97", "97")]

    single = mod.score(sig, bars, stop_mult=Decimal(1))
    double = mod.score(sig, bars, stop_mult=Decimal(2))

    assert single is not None and double is not None
    assert single.label == "FAILED" and double.label == "FAILED"
    assert single.r == Decimal(-1)
    assert double.r == Decimal(-2), (
        "a 2x stop charged itself one unit, so widening the stop would read as "
        "free -- the comparison between configurations is then worthless"
    )


def test_a_win_pays_the_targets_distance_in_original_stops(mod) -> None:
    sig = _signal(mod)
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "100", "104")]

    out = mod.score(sig, bars)

    assert out is not None
    assert out.label == "SUCCESS"
    assert out.r == Decimal(4), "entry 100, stop 1.0, target 104 -- that is 4R"


def test_an_r_target_is_measured_from_the_original_stop_too(mod) -> None:
    sig = _signal(mod)
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "102", "100", "102")]

    out = mod.score(sig, bars, target_r=Decimal(2))

    assert out is not None
    assert out.label == "SUCCESS"
    assert out.r == Decimal(2)


# --- the boundaries ------------------------------------------------------


def test_one_candle_holding_both_target_and_stop_is_scored_a_loss(mod) -> None:
    """OHLC cannot order them, and a win here would flatter the wide stops.

    A stop wide enough that both levels fit inside one bar is exactly the
    configuration this choice must not reward.
    """
    sig = _signal(mod)
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "97", "97")]

    out = mod.score(sig, bars)

    assert out is not None
    assert out.label == "FAILED", "the ambiguous candle was resolved as a win"


def test_price_cannot_reach_the_stop_without_passing_the_entry(mod) -> None:
    """Why there is no "invalidated before entry" branch, pinned.

    A first draft had one and it was dead code. For a long the stop sits BELOW
    the entry, so "entry not touched" means `low > entry`, and then
    `close >= low > entry > stop` -- the stop cannot be broken. Under an OHLC
    fill model the invalidation is unreachable without a fill first, in both
    directions, so the engine's own INVALIDATED_EARLY signals surface as
    control disagreements rather than being silently zeroed.

    This is the `checks-that-cannot-fail` family, found in this very tool.
    """
    sig = _signal(mod)
    # A candle that dives through entry 100 to close at 98, under the stop, and
    # a following candle that stays there. The dive only fills (see the
    # entry-candle test below); the loss is taken on the candle after it.
    bars = [
        _bar(mod, "103", "101", "102"),
        _bar(mod, "102", "98", "98"),
        _bar(mod, "98.5", "97.5", "98"),
    ]

    out = mod.score(sig, bars)

    assert out is not None
    assert out.label == "FAILED", (
        "the entry was touched on the way down, so this is a filled loss -- "
        "not an unfilled invalidation"
    )
    assert out.r == Decimal(-1)

    short = _signal(mod, "DOWN", entry="100", inval="101", target="96")
    rising = [
        _bar(mod, "99", "97", "98"),
        _bar(mod, "102", "98", "102"),
        _bar(mod, "102.5", "101.5", "102"),
    ]

    mirrored = mod.score(short, rising)

    assert mirrored is not None
    assert mirrored.label == "FAILED", "the short mirrors it: entry 100 is crossed first"


def test_the_entry_candle_only_activates_even_when_it_closes_through_the_stop(mod) -> None:
    """The engine's convention, which the control depends on.

    `lifecycle/state.py::observe()` returns ACTIVE the moment the entry is
    touched and reads neither the invalidation nor the target on that candle.
    Judging the entry candle too is a different rule, and not a slightly
    different one: a tight zone-edge stop is often closed through on the very
    candle that fills it, which turned M5's zone-edge stop from +0.71% into
    -11.29% (n=15; SLS erratum PR #312).
    """
    sig = _signal(mod)
    # Bar 1 touches entry 100 AND closes at 98, through the stop at 99. Bar 2
    # recovers and touches neither level.
    bars = [_bar(mod, "100.5", "98", "98"), _bar(mod, "100", "99.5", "99.8")]

    out = mod.score(sig, bars, ttl=2)

    assert out is not None
    assert out.label == "EXPIRED_ACTIVE", (
        "the entry candle was judged for the stop -- the engine only activates "
        "on it, so this resolved a candle earlier than the engine would"
    )
    assert out.r == Decimal("-0.2"), "marked to bar 2's close, 99.8"

    # The touch convention is held to the same rule: a wick through the stop on
    # the entry candle is not a loss either.
    touch = mod.score(sig, bars, ttl=2, close_stop=False)

    assert touch is not None
    assert touch.label == "EXPIRED_ACTIVE"

    # And the target: an entry candle that also reaches it is not a win.
    wide = [_bar(mod, "104", "100", "103"), _bar(mod, "103", "101", "102")]
    target_on_entry = mod.score(sig, wide, ttl=2)

    assert target_on_entry is not None
    assert target_on_entry.label == "EXPIRED_ACTIVE"

    # The short mirrors all of it.
    short = _signal(mod, "DOWN", entry="100", inval="101", target="96")
    mirrored = mod.score(
        short, [_bar(mod, "102", "99.5", "102"), _bar(mod, "100.5", "100", "100.2")], ttl=2
    )

    assert mirrored is not None
    assert mirrored.label == "EXPIRED_ACTIVE"
    assert mirrored.r == Decimal("-0.2")


def test_an_entry_never_touched_and_never_invalidated_expires_flat(mod) -> None:
    sig = _signal(mod)
    bars = [_bar(mod, "103", "101", "102")] * 5

    out = mod.score(sig, bars, ttl=5)

    assert out is not None
    assert out.label == "EXPIRED_UNTOUCHED"
    assert out.r == Decimal(0)


def test_an_unresolved_trade_is_marked_to_the_last_close_not_dropped(mod) -> None:
    """Dropping them would favour whatever leaves most trades open.

    A wider stop and a longer TTL both do exactly that, so discarding the
    unresolved ones would quietly reward the two levers under test.
    """
    sig = _signal(mod)
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "102", "100", "101.5")]

    out = mod.score(sig, bars, ttl=2)

    assert out is not None
    assert out.label == "EXPIRED_ACTIVE"
    assert out.r == Decimal("1.5"), "entry 100, stop 1.0, last close 101.5"


def test_the_touch_and_close_conventions_can_disagree(mod) -> None:
    """CLAUDE.md records PF 1.121 close-based against 0.697 touch-based.

    A file that silently used one of them would be answering a different
    question from the engine, whose §12 rule is close-through.
    """
    sig = _signal(mod)
    # The low pierces the stop; the close does not.
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "100", "98.5", "99.5")]

    close_based = mod.score(sig, bars, close_stop=True)
    touch_based = mod.score(sig, bars, close_stop=False)

    assert close_based is not None and touch_based is not None
    assert close_based.label != "FAILED"
    assert touch_based.label == "FAILED"


def test_a_down_signal_is_scored_the_mirror_of_an_up_one(mod) -> None:
    sig = _signal(mod, "DOWN", entry="100", inval="101", target="96")
    bars = [_bar(mod, "100", "100", "100"), _bar(mod, "100", "96", "96")]

    out = mod.score(sig, bars)

    assert out is not None
    assert out.label == "SUCCESS"
    assert out.r == Decimal(4)


def test_an_unscoreable_signal_returns_none_rather_than_a_flat_result(mod) -> None:
    """A zero result would dilute every average by however many exist."""
    assert mod.score(_signal(mod), []) is None
    assert mod.score(_signal(mod, entry="100", inval="100"), [_bar(mod, "1", "1", "1")]) is None


# --- aggregation ---------------------------------------------------------


def test_expectancy_averages_over_filled_positions_only(mod) -> None:
    """The two zero-R labels are counted but must not enter the denominator."""
    filled = _signal(mod)
    filled_bars = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "100", "104")]

    unfilled = _signal(mod, signal_id="SIG2")
    unfilled_bars = [_bar(mod, "103", "101", "102")] * 3

    result = mod.tally(
        [filled, unfilled],
        {"SIG": filled_bars, "SIG2": unfilled_bars},
        ttl=3,
    )

    assert result.counts["SUCCESS"] == 1
    assert result.counts["EXPIRED_UNTOUCHED"] == 1
    assert result.scored == 1, "the unfilled signal entered the expectancy denominator"
    assert result.per_trade == Decimal(4)


def test_the_hit_rate_is_offered_but_is_not_the_expectancy(mod) -> None:
    """Both are reported because either alone misleads; see the module docstring."""
    result = mod.Tally(
        counts={},
        total_r=Decimal("-9"),
        per_trade=Decimal("-0.243"),
        wins=20,
        losses=17,
        scored=37,
    )

    assert result.hit_rate is not None
    assert result.hit_rate > Decimal(50)
    assert result.per_trade < 0, (
        "the measured case: a 54% hit rate still losing 0.243R a trade, which is "
        "why hit rate is never reported on its own"
    )


def test_concentration_strips_the_best_trades_in_order(mod) -> None:
    """One trade carrying a result is the trap this exists to expose."""
    sigs, bars = [], {}

    # Three winners at 4R and one loser at -1R: total +11R over 4 trades.
    for i in range(3):
        sid = f"W{i}"
        sigs.append(_signal(mod, signal_id=sid))
        bars[sid] = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "100", "104")]

    sigs.append(_signal(mod, signal_id="L"))
    bars["L"] = [_bar(mod, "100", "100", "100"), _bar(mod, "100", "97", "97")]

    rows = mod.concentration(sigs, bars)

    assert [r[0] for r in rows] == [0, 1, 2, 3]
    assert rows[0][1] == Decimal(11)
    assert rows[1][1] == Decimal(7), "dropping the best winner must remove 4R"
    assert rows[3][1] == Decimal(-1), "with every winner gone only the loser is left"


# --- the control gate ----------------------------------------------------


def test_the_control_gate_blocks_a_simulator_that_stopped_matching(mod) -> None:
    """A drifted simulator still prints a plausible table, which is the danger."""
    sigs, bars = [], {}

    for i in range(10):
        sid = f"S{i}"
        # Every one really wins, but the engine recorded FAILED.
        sigs.append(_signal(mod, label="FAILED", signal_id=sid))
        bars[sid] = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "100", "104")]

    assert mod.control_passes(sigs, bars) is False


def test_the_control_gate_passes_when_the_labels_agree(mod) -> None:
    sigs, bars = [], {}

    for i in range(10):
        sid = f"S{i}"
        sigs.append(_signal(mod, label="SUCCESS", signal_id=sid))
        bars[sid] = [_bar(mod, "100", "100", "100"), _bar(mod, "104", "100", "104")]

    assert mod.control_passes(sigs, bars) is True


def test_the_control_gate_refuses_when_there_is_nothing_resolved_to_check(mod) -> None:
    """No evidence is not agreement, and must not read as a pass."""
    sig = _signal(mod, label="PENDING")

    assert mod.control_passes([sig], {"SIG": [_bar(mod, "100", "100", "100")]}) is False


def test_the_candle_fetch_is_sized_for_the_longest_ttl_swept(mod) -> None:
    """A sweep asking for more candles than are fetched would read the
    shortfall as `EXPIRED_ACTIVE` and flatter every long-TTL row."""
    longest = max(geometry.get("ttl", 24) for _, geometry in mod.sweeps())

    assert longest <= mod.MAX_TTL, (
        f"sweeps() asks for TTL {longest} but only {mod.MAX_TTL} candles are loaded"
    )
