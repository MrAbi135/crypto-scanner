"""§3.4's idle edge belongs to the pipeline, and to exactly one place in it.

`RANGING additionally applies when price has closed inside the current external
dealing range without external BOS for P.structure.idle_candles = 100 closed
candles` needs three facts and one state, and no single engine holds all four.
The structure engine holds the bracket, the closes and the breaks; the shift
engine holds the trend, detects no BOS and reads no events; and the shift engine
cannot ask the structure engine, because `structure_replay.py` already imports
`trend_after` from it. So the condition is reported by the engine whose facts it
is, and the edge is applied where the authoritative trend is known.

These drive `DetectionPipeline.run` with stub engines, because the thing under
test is the join -- which value reaches confluence -- not either engine's own
arithmetic. `idle_condition` has its own tests below it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.application.detection.pipeline import DetectionPipeline
from scanner.application.detection.structure_replay import idle_condition
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength, TrendState
from scanner.shared import Timeframe

START = datetime(2026, 1, 1, tzinfo=UTC)

END = START + timedelta(hours=8)


@dataclass
class _StructureReport:
    idle_by_34: bool
    candles: int = 501
    events_inserted: int = 0
    trend_state: str = "RANGING"


@dataclass
class _ShiftReport:
    trend_state: str


class _Stub:
    """One engine's `run`, returning whatever it was handed."""

    def __init__(self, report: object = None) -> None:
        self._report = report if report is not None else object()

    async def run(self, *args: object, **kwargs: object) -> object:
        return self._report


class _Confluence:
    """Records the `trend_state` the pipeline passed -- the whole point."""

    def __init__(self) -> None:
        self.seen: str | None = None

    async def run(self, *args: object, trend_state: str, **kwargs: object) -> object:
        self.seen = trend_state

        return object()


def _pipeline(*, idle: bool, shift_trend: str) -> tuple[DetectionPipeline, _Confluence]:
    confluence = _Confluence()

    pipeline = DetectionPipeline(
        structure=_Stub(_StructureReport(idle_by_34=idle)),
        liquidity=_Stub(),
        structure_shift=_Stub(_ShiftReport(trend_state=shift_trend)),
        ict=_Stub(),
        ict_ote=_Stub(),
        ict_ob=_Stub(),
        ict_interaction=_Stub(),
        participation=_Stub(),
        confluence=confluence,
    )

    return pipeline, confluence


async def _run(*, idle: bool, shift_trend: str) -> tuple[str, str]:
    """Returns (what confluence saw, what the report published)."""
    pipeline, confluence = _pipeline(idle=idle, shift_trend=shift_trend)

    report = await pipeline.run("BTCUSDT", Timeframe.M15, START, END)

    assert confluence.seen is not None

    return confluence.seen, report.trend_state


@pytest.mark.asyncio
@pytest.mark.parametrize("trend", ["BULLISH", "BEARISH"])
async def test_an_idle_directional_trend_reaches_confluence_as_ranging(trend: str) -> None:
    """§3.4's two edges, and the reason this change exists."""
    seen, published = await _run(idle=True, shift_trend=trend)

    assert seen == TrendState.RANGING.value, (
        f"shift said {trend} and §3.4's condition held, so the authoritative "
        "state is RANGING -- confluence must be given that, not the pre-edge value"
    )
    assert published == TrendState.RANGING.value


@pytest.mark.asyncio
@pytest.mark.parametrize("trend", ["BULLISH", "BEARISH"])
async def test_a_directional_trend_that_is_not_idle_passes_through(trend: str) -> None:
    seen, published = await _run(idle=False, shift_trend=trend)

    assert seen == trend
    assert published == trend


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trend",
    ["RANGING", "BULLISH_CAUTION", "BEARISH_CAUTION"],
)
async def test_only_the_two_edges_34_draws_are_eligible(trend: str) -> None:
    """RANGING is the destination; a CAUTION state is mid-transition.

    Idling out of BULLISH_CAUTION would discard the CHoCH that put it there,
    which §3.4's diagram does not draw and §3.6 relies on.
    """
    seen, published = await _run(idle=True, shift_trend=trend)

    assert seen == trend, f"{trend} is not one of §3.4's idle edges"
    assert published == trend


@pytest.mark.asyncio
async def test_the_shift_engines_own_report_is_left_untouched() -> None:
    """The edge is applied at the join, not written back into the engine.

    If the pipeline mutated the shift report, the two would disagree about what
    the shift engine decided -- and the shift engine's persisted state, which
    every other reader still takes, would no longer match its own report.
    """
    pipeline, confluence = _pipeline(idle=True, shift_trend="BEARISH")

    report = await pipeline.run("BTCUSDT", Timeframe.M15, START, END)

    assert report.structure_shift.trend_state == "BEARISH"
    assert report.trend_state == TrendState.RANGING.value
    assert confluence.seen == TrendState.RANGING.value


# --- the condition itself -------------------------------------------------
#
# The flat-ATR recipe is not needed here: §3.4's condition reads closes and
# swing prices, never ATR. Bars are shaped so the bracket is unambiguous.


def _series(closes: list[str], *, low: str, high: str) -> list:
    """`closes` as a series wide enough for §3.4, with a known bracket."""
    tf = Timeframe.H1
    out = []

    for index, close in enumerate(closes):
        price = Decimal(close)
        out.append(
            make_candle(
                symbol="IDLEUSDT",
                timeframe=tf,
                open_time=START + tf.duration * index,
                open_=price,
                close=price,
                high=max(price, Decimal(high)),
                low=min(price, Decimal(low)),
            )
        )

    return out


def _swings(*, low: str, high: str, count: int) -> tuple[SwingPoint, ...]:
    return (
        SwingPoint(
            index=count - 2,
            open_time=START + Timeframe.H1.duration * (count - 2),
            price=Decimal(low),
            kind=SwingKind.LOW,
            strength=SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            index=count - 1,
            open_time=START + Timeframe.H1.duration * (count - 1),
            price=Decimal(high),
            kind=SwingKind.HIGH,
            strength=SwingStrength.EXTERNAL,
        ),
    )


def test_the_condition_holds_when_every_close_is_inside_and_nothing_broke() -> None:
    closes = ["100"] * 120
    series = _series(closes, low="90", high="110")

    assert idle_condition(
        candles=series,
        external_swings=_swings(low="90", high="110", count=len(series)),
        broke_at=frozenset(),
    )


def test_one_close_outside_the_bracket_is_enough_to_deny_it() -> None:
    closes = ["100"] * 120
    closes[-50] = "111"
    series = _series(closes, low="90", high="112")

    assert not idle_condition(
        candles=series,
        external_swings=_swings(low="90", high="110", count=len(series)),
        broke_at=frozenset(),
    ), "a close above the bracket high is not inside the dealing range"


def test_a_break_inside_the_window_denies_it_even_when_every_close_is_inside() -> None:
    """The term that cannot be dropped.

    Over 91,591 replayed passes the containment term alone held 2,273 times and
    a BOS sat inside the span in 981 of them -- so this is the 43% case, not an
    edge case.
    """
    closes = ["100"] * 120
    series = _series(closes, low="90", high="110")
    swings = _swings(low="90", high="110", count=len(series))

    assert not idle_condition(
        candles=series,
        external_swings=swings,
        broke_at=frozenset({len(series) - 5}),
    )

    assert idle_condition(
        candles=series,
        external_swings=swings,
        broke_at=frozenset({len(series) - 105}),
    ), "a break older than the 100-candle span does not keep the market busy"


def test_a_missing_side_of_the_bracket_denies_it() -> None:
    series = _series(["100"] * 120, low="90", high="110")
    lows_only = (_swings(low="90", high="110", count=len(series))[0],)

    assert not idle_condition(candles=series, external_swings=lows_only, broke_at=frozenset()), (
        "with no confirmed high there is no dealing range to be inside of"
    )


def test_an_inverted_bracket_denies_it() -> None:
    series = _series(["100"] * 120, low="90", high="110")

    assert not idle_condition(
        candles=series,
        external_swings=_swings(low="115", high="105", count=len(series)),
        broke_at=frozenset(),
    ), "low above high is not a range"


def test_a_series_shorter_than_the_idle_window_claims_nothing() -> None:
    """Too early to tell is not the same as idle."""
    series = _series(["100"] * 40, low="90", high="110")

    assert not idle_condition(
        candles=series,
        external_swings=_swings(low="90", high="110", count=len(series)),
        broke_at=frozenset(),
    )
