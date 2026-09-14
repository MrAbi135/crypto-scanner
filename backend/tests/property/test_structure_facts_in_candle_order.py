"""Structure facts do not depend on which pass wrote them (audit M1+M2, s4-v10).

Until s4-v10 every pass decided the whole 500-candle window again. Breaks were
gated by the shift engine's latest trend applied to all of it, so each flip
back-wrote the other direction's breaks for days; and the window start
relabelled its first swing SEED and demoted external pivots to internal.
Offline, a sliding window wrote 46 structure facts in 250 passes that one pass
over the same candles never writes: 33 internal twins of promoted pivots,
8 breaks and 5 failed breaks.

So: structure and shift run close by close in pipeline order over a sliding
window, and must write the structure facts one pass writes when its breaks are
gated by one shift pass's trend path.
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
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    StructureShiftReplayService,
)
from scanner.domain.common import Candle

pytestmark = pytest.mark.property

# Above §1.9's 300-candle warm gate; series no longer than two windows keep the
# whole history inside the snapshots' one-window carry.
_WINDOW = 300

_STRUCTURE = (
    "SWING_",
    "STRUCTURE_INTERNAL_",
    "STRUCTURE_EXTERNAL_",
    "BOS_",
    "STRUCTURE_FAILED_BREAK_",
)

# Window offsets name a position in whichever window wrote the fact.
_WINDOW_OFFSETS = frozenset({"index", "swing_index", "break_index", "failed_index"})


class _Clock:
    def __init__(self, series: list[Candle]) -> None:
        self._series = series

    def now(self):  # type: ignore[no-untyped-def]
        return self._series[-1].close_time


def _engines(
    series: list[Candle],
) -> tuple[StructureReplayService, StructureShiftReplayService, InMemoryEngineEventRepository]:
    events = InMemoryEngineEventRepository()
    candles = InMemoryCandleRepository(series)
    shift_store = InMemoryEngineStateStore()
    clock = _Clock(series)

    structure = StructureReplayService(
        candles,
        events,
        EngineStateManager(InMemoryEngineStateStore()),
        clock,  # type: ignore[arg-type]
        shift_state=EngineStateManager(shift_store, namespace=SHIFT_NAMESPACE),
        shift_algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
    )
    shift = StructureShiftReplayService(
        candles,
        events,
        InMemoryIctEvidenceRepository(events, InMemoryLiquidityTransitionRepository()),
        clock,  # type: ignore[arg-type]
        EngineStateManager(shift_store, namespace=SHIFT_NAMESPACE),
    )

    return structure, shift, events


def _facts(
    events: InMemoryEngineEventRepository, lower: str, upper: str
) -> list[tuple[str, str, str]]:
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
        if e.event_type.startswith(_STRUCTURE) and lower <= e.event_at.isoformat() < upper
    )


@settings(
    max_examples=6,
    # Derandomized so a mutation battery run is a verdict, not a draw.
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(series=walking_candle_series(min_size=_WINDOW + 40, max_size=_WINDOW + 70))
def test_close_by_close_structure_writes_what_one_pass_writes(series: list[Candle]) -> None:
    step = series[0].timeframe.duration
    symbol, timeframe = series[0].symbol, series[0].timeframe

    async def both() -> tuple[list, list]:
        structure, shift, sliding = _engines(series)

        for index in range(_WINDOW, len(series)):
            start = series[index].open_time - step * _WINDOW
            end = series[index].open_time + step

            if index == _WINDOW:
                # A running system: the shift engine has walked before.
                await shift.run(symbol, timeframe, start, end)

            await structure.run(symbol, timeframe, start, end)
            await shift.run(symbol, timeframe, start, end)

        structure_once, shift_once, once = _engines(series)
        whole = (series[0].open_time, series[-1].open_time + step)
        await shift_once.run(symbol, timeframe, *whole)
        await structure_once.run(symbol, timeframe, *whole)

        # From the first window's end: the sliding run first writes there. Up to
        # the newest candle, exclusive: its break is decided by the next pass.
        lower, upper = series[_WINDOW].open_time.isoformat(), series[-1].open_time.isoformat()

        return _facts(sliding, lower, upper), _facts(once, lower, upper)

    sliding, once = asyncio.run(both())

    event(f"structure facts: {min(len(once) // 10 * 10, 60)}+")
    event(f"breaks: {min(sum(1 for fact in once if fact[0].startswith('BOS_')), 3)}")

    assert sliding == once, (
        f"only sliding: {sorted(set(sliding) - set(once))[:4]}\n"
        f"only one pass: {sorted(set(once) - set(sliding))[:4]}"
    )
