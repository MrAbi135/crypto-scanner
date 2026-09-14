"""The shift engine's history does not depend on where its window starts (audit M6).

The engine replays a trailing window on every close. Until s6-structure-shift-v5
each pass rebuilt the trend from RANGING at the window's first candle, so the
trend path -- and every CHoCH and MSS on it -- depended on that start: BTCUSDT H1
replayed 08-06..08-10 as BULLISH in one window and BEARISH in the window one
candle later, and a sliding 150-candle window disagreed with a window anchored
at the first candle on the trend in 59 of 300 passes.

So: a close-by-close run over a window shorter than the series, resuming from
its own snapshot, must leave the same facts and the same final trend as one
pass over the whole series.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from hypothesis import HealthCheck, event, given, settings
from tests.golden.harness.memory import (
    InMemoryCandleRepository,
    InMemoryEngineEventRepository,
    InMemoryEngineStateStore,
    InMemoryIctEvidenceRepository,
    InMemoryLiquidityTransitionRepository,
)
from tests.support.strategies import walking_candle_series

from scanner.application.detection.state import SHIFT_NAMESPACE, EngineStateManager
from scanner.application.detection.structure_shift_replay import StructureShiftReplayService
from scanner.domain.common import Candle

pytestmark = pytest.mark.property

# The snapshot carries one window of confirmed swings from before the current
# window (500 candles in production). Series no longer than two windows keep
# the whole history inside that bound, so a single pass is the exact reference.
_WINDOW = 100


class _Clock:
    def __init__(self, series: list[Candle]) -> None:
        self._series = series

    def now(self):  # type: ignore[no-untyped-def]
        return self._series[-1].close_time


def _service(
    series: list[Candle],
) -> tuple[StructureShiftReplayService, InMemoryEngineEventRepository]:
    events = InMemoryEngineEventRepository()

    service = StructureShiftReplayService(
        InMemoryCandleRepository(series),
        events,
        InMemoryIctEvidenceRepository(events, InMemoryLiquidityTransitionRepository()),
        _Clock(series),  # type: ignore[arg-type]
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    return service, events


# Window offsets in the payloads (`break_index`, `swing_index`, `choch_index`,
# `followthrough_index`) name a position in whichever window wrote the fact, so
# they differ between a sliding window and one pass for the same fact.
_WINDOW_OFFSETS = frozenset({"break_index", "swing_index", "choch_index", "followthrough_index"})


def _facts(events: InMemoryEngineEventRepository) -> list[tuple[str, str, str]]:
    return sorted(
        (
            e.event_type,
            e.event_at.isoformat(),
            json.dumps(
                {k: v for k, v in json.loads(e.payload).items() if k not in _WINDOW_OFFSETS},
                sort_keys=True,
            ),
        )
        for e in events.events
    )


@settings(
    max_examples=30,
    # Derandomized so a mutation battery run is a verdict, not a draw.
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(series=walking_candle_series(min_size=_WINDOW + 60, max_size=_WINDOW + 140))
def test_a_sliding_window_that_resumes_leaves_one_pass_history(series: list[Candle]) -> None:
    async def both() -> tuple[list, str, list, str]:
        step = series[0].timeframe.duration

        sliding, sliding_events = _service(series)
        trend = "RANGING"

        for index in range(_WINDOW, len(series)):
            candle = series[index]
            report = await sliding.run(
                candle.symbol,
                candle.timeframe,
                candle.open_time - step * _WINDOW,
                candle.open_time + step,
            )
            trend = report.trend_state

        once, once_events = _service(series)
        whole = await once.run(
            series[0].symbol,
            series[0].timeframe,
            series[0].open_time,
            series[-1].open_time + step,
        )

        return _facts(sliding_events), trend, _facts(once_events), whole.trend_state

    sliding, sliding_trend, once, once_trend = asyncio.run(both())

    # The close-by-close run only ever writes facts about candles its windows
    # reached; the single pass also writes them for the first window's candles.
    first_window_end = series[_WINDOW].open_time.isoformat()
    once = [fact for fact in once if fact[1] >= first_window_end]
    sliding = [fact for fact in sliding if fact[1] >= first_window_end]

    event(f"shift facts: {min(len(once), 6)}")
    event(f"final trend: {once_trend}")

    assert sliding == once, (
        f"only sliding: {sorted(set(sliding) - set(once))[:4]}\n"
        f"only one pass: {sorted(set(once) - set(sliding))[:4]}"
    )
    assert sliding_trend == once_trend
