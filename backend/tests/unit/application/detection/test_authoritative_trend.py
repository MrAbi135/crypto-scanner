"""§3.4's authoritative state is published once and read by everyone else.

#299 applied §3.4's idle edge in the pipeline but kept the result in memory, so
the two readers that cannot be inside a pass still took the shift engine's
pre-edge value: the soak suite's check A (tracker row 22) and F6's read of the
rung above (row 23). Both now read what the pipeline publishes.

Three properties are worth a test, and they are the three that could go wrong
quietly:

  * the pipeline writes the value it gave confluence, not the pre-edge one;
  * F6 prefers that value over the shift record;
  * an absent record falls back to the shift trend and is NOT read as RANGING
    -- inventing RANGING would deny every alignment on exactly the passes with
    no evidence either way (a first pass after a deploy, the golden harness).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore

from scanner.application.detection.pipeline import DetectionPipeline
from scanner.application.detection.state import (
    AUTHORITATIVE_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.domain.structure import TrendState
from scanner.shared import Timeframe

START = datetime(2026, 1, 1, tzinfo=UTC)

END = START + timedelta(hours=8)

SHIFT_VERSION = "s6-structure-shift-vTEST"


@dataclass
class _StructureReport:
    idle_by_34: bool
    last_processed_open_time: datetime | None = START
    candles: int = 501
    events_inserted: int = 0
    trend_state: str = "RANGING"


@dataclass
class _ShiftReport:
    trend_state: str


class _Stub:
    def __init__(self, report: object = None) -> None:
        self._report = report if report is not None else object()

    async def run(self, *args: object, **kwargs: object) -> object:
        return self._report


class _Confluence:
    def __init__(self) -> None:
        self.seen: str | None = None

    async def run(self, *args: object, trend_state: str, **kwargs: object) -> object:
        self.seen = trend_state

        return object()


def _manager() -> EngineStateManager:
    return EngineStateManager(
        InMemoryEngineStateStore(),
        namespace=AUTHORITATIVE_NAMESPACE,
    )


async def _run(
    *,
    idle: bool,
    shift_trend: str,
    state: EngineStateManager | None,
    version: str | None = SHIFT_VERSION,
) -> tuple[str, object]:
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
        authoritative_state=state,
        authoritative_version=version,
    )

    report = await pipeline.run("BTCUSDT", Timeframe.M15, START, END)

    assert confluence.seen is not None

    return confluence.seen, report


@pytest.mark.asyncio
async def test_the_published_value_is_the_one_confluence_was_given() -> None:
    """The edged value, not the shift engine's own."""
    state = _manager()

    seen, _ = await _run(idle=True, shift_trend="BEARISH", state=state)

    saved = await state.load("BTCUSDT", Timeframe.M15.value, SHIFT_VERSION)

    assert seen == TrendState.RANGING.value
    assert saved is not None
    assert saved.trend_state == TrendState.RANGING.value, (
        "the pipeline published the pre-edge trend, so every reader outside the "
        "pass would disagree with the one inside it"
    )


@pytest.mark.asyncio
async def test_a_trend_that_was_not_idled_is_published_unchanged() -> None:
    state = _manager()

    seen, _ = await _run(idle=False, shift_trend="BULLISH", state=state)

    saved = await state.load("BTCUSDT", Timeframe.M15.value, SHIFT_VERSION)

    assert seen == "BULLISH"
    assert saved is not None
    assert saved.trend_state == "BULLISH"


@pytest.mark.asyncio
async def test_without_a_store_the_pass_still_runs_and_reports() -> None:
    """The golden harness and `engine run` drive the pipeline with no store."""
    seen, report = await _run(idle=True, shift_trend="BEARISH", state=None)

    assert seen == TrendState.RANGING.value
    assert report.trend_state == TrendState.RANGING.value


@pytest.mark.asyncio
async def test_a_store_without_a_version_writes_nothing() -> None:
    """Both halves or neither -- a record under the wrong key is worse than none."""
    state = _manager()

    await _run(idle=True, shift_trend="BEARISH", state=state, version=None)

    assert await state.load("BTCUSDT", Timeframe.M15.value, SHIFT_VERSION) is None


# --- F6's side of it -----------------------------------------------------


class _Loader:
    """Just enough of `EngineStateManager` for `_read_htf_state`."""

    def __init__(self, trend: str | None) -> None:
        self._trend = trend
        self.asked = 0

    async def load(self, symbol: str, timeframe: str, algo_version: str):
        self.asked += 1

        if self._trend is None:
            return None

        return StructureEngineState(
            symbol=symbol,
            timeframe=timeframe,
            algo_version=algo_version,
            trend_state=self._trend,
        )


class _Htf:
    """`_read_htf_state` bound to nothing but its two stores."""

    def __init__(
        self,
        *,
        authoritative: str | None,
        shift: str | None,
        wire_authoritative: bool = True,
    ) -> None:
        from scanner.application.detection.confluence_replay import ConfluenceReplayService

        self._read = ConfluenceReplayService._read_htf_state.__get__(self)
        # `wire_authoritative=False` is "no store at all" (the golden harness);
        # a wired store returning None is "nobody has published this rung yet"
        # (a first pass after a deploy). Those are different code paths and the
        # second is the one that will actually happen in production.
        self._authoritative_state = _Loader(authoritative) if wire_authoritative else None
        self._shift_state = _Loader(shift)
        self._shift_algo_version = SHIFT_VERSION

    async def __call__(self, timeframe: Timeframe = Timeframe.M5) -> str | None:
        return await self._read("BTCUSDT", timeframe)


@pytest.mark.asyncio
async def test_f6_prefers_the_authoritative_value_over_the_shift_record() -> None:
    """Row 23: an idled rung must not keep contributing its old direction."""
    htf = _Htf(authoritative="RANGING", shift="BULLISH")

    assert await htf() == "RANGING", (
        "F6 read the shift record, so a rung §3.4 had idled still scored as a "
        "trend -- and `htf_aligned` could hold for a ranging market"
    )


@pytest.mark.asyncio
async def test_f6_falls_back_when_the_store_is_wired_but_the_rung_is_unpublished() -> None:
    """The production case: the store exists, this rung has no record yet.

    Absent is 'nobody has said yet', not 'RANGING'. Reading it as RANGING would
    deny every alignment on the first pass after a deploy.
    """
    htf = _Htf(authoritative=None, shift="BULLISH")

    assert htf._authoritative_state is not None, "this test needs the store wired"
    assert await htf() == "UP"
    assert htf._authoritative_state.asked == 1, "the authoritative store was not consulted"


@pytest.mark.asyncio
async def test_f6_falls_back_when_no_authoritative_store_is_wired_at_all() -> None:
    """The golden harness and `engine run` drive confluence without one."""
    htf = _Htf(authoritative=None, shift="BULLISH", wire_authoritative=False)

    assert await htf() == "UP"


@pytest.mark.asyncio
async def test_f6_still_reports_nothing_when_neither_store_has_the_rung() -> None:
    htf = _Htf(authoritative=None, shift=None)

    assert await htf() is None


@pytest.mark.asyncio
async def test_f6_reports_nothing_at_the_top_of_the_ladder() -> None:
    """H4's rung above is D1, which is not ingested; W1 has none at all."""
    htf = _Htf(authoritative="RANGING", shift="BULLISH")

    assert await htf(Timeframe.W1) is None
