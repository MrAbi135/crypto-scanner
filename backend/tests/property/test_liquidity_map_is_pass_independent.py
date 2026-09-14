"""The liquidity map does not depend on which passes ran (audit M5/M10, s5-v13).

The engine replays one close at a time. Every pass sees the candles a single
pass over the whole series would see, up to its own close -- so, with the
window start held still, running close by close and running once at the end
must leave the same pools, the same transitions and the same published facts.

Before s5-v13 they did not, and on real data the difference was large: offline,
1 to 12 pools per context existed only in the close-by-close replay, and on the
host 29 cluster pools were ACTIVE beside their own member's swing pool. The map
was rebuilt each pass from the pools still ACTIVE at that moment, with the
newest candle's epsilon, in loop order, so what an earlier pass had built -- or
had since seen consumed -- decided what a later pass built.

What is compared is what a pass cannot legitimately revise. A pool's strength,
class and evidence are current state and stop being rewritten once it is
terminal, so a pool consumed on an early pass keeps the numbers it had then;
those fields are left out.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hypothesis import HealthCheck, event, given, settings
from tests.golden.harness.memory import (
    InMemoryCandleRepository,
    InMemoryEngineEventRepository,
    InMemoryIctEvidenceRepository,
    InMemoryLiquidityPoolRepository,
    InMemoryLiquidityStateStore,
    InMemoryLiquidityTransitionRepository,
)
from tests.support.strategies import walking_candle_series

from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.domain.common import Candle

pytestmark = pytest.mark.property

_WARM = 300


class _Clock:
    def __init__(self) -> None:
        self.moment = None

    def now(self):  # type: ignore[no-untyped-def]
        return self.moment


class _Stores:
    def __init__(self) -> None:
        self.pools = InMemoryLiquidityPoolRepository()
        self.transitions = InMemoryLiquidityTransitionRepository()
        self.events = InMemoryEngineEventRepository()
        self.evidence = InMemoryIctEvidenceRepository(self.events, self.transitions, self.pools)
        self.clock = _Clock()

    async def run(self, series: list[Candle], end: int) -> None:
        window = series[:end]
        self.clock.moment = window[-1].close_time

        await LiquidityReplayService(
            InMemoryCandleRepository(window),
            self.pools,
            self.transitions,
            self.events,
            InMemoryLiquidityStateStore(),
            self.evidence,
            self.clock,  # type: ignore[arg-type]
        ).run(
            window[0].symbol,
            window[0].timeframe,
            window[0].open_time,
            window[-1].open_time + window[-1].timeframe.duration,
        )

    def outcome(self) -> dict[str, Any]:
        return {
            "pools": sorted(
                (
                    p.pool_id,
                    p.side,
                    p.source,
                    str(p.price),
                    str(p.band_low),
                    str(p.band_high),
                    p.member_count,
                    p.created_at.isoformat(),
                    p.state,
                )
                for p in self.pools.pools.values()
            ),
            "transitions": sorted(
                (t.pool_id, t.to_state, t.reason, t.transitioned_at.isoformat(), t.evidence)
                for t in self.transitions.transitions
            ),
            "events": sorted(
                (e.event_key, e.event_type, e.event_at.isoformat(), e.payload)
                for e in self.events.events
            ),
        }


@settings(
    max_examples=12,
    # Derandomized so a mutation battery run is a verdict, not a draw.
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(series=walking_candle_series(min_size=_WARM + 20, max_size=_WARM + 50))
def test_close_by_close_leaves_the_map_one_pass_builds(series: list[Candle]) -> None:
    async def both() -> tuple[dict[str, Any], dict[str, Any]]:
        stepped = _Stores()

        for end in range(_WARM, len(series) + 1):
            await stepped.run(series, end)

        once = _Stores()
        await once.run(series, len(series))

        return stepped.outcome(), once.outcome()

    stepped, once = asyncio.run(both())

    event(f"pools: {min(len(once['pools']) // 10 * 10, 50)}+")

    for part in ("pools", "transitions", "events"):
        only_stepped = sorted(set(stepped[part]) - set(once[part]))
        only_once = sorted(set(once[part]) - set(stepped[part]))

        assert not only_stepped and not only_once, (
            f"{part} differ between close-by-close and one pass over {len(series)} candles:\n"
            f"  only close-by-close: {only_stepped[:5]}\n"
            f"  only one pass:       {only_once[:5]}"
        )
