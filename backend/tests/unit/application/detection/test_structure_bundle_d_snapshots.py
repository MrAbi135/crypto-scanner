"""Old snapshots meeting the Bundle D code (SLS v1.0.20, M1).

The shift snapshot gained fields (surpassed levels, the leg, the last CHoCH,
the leg's candles, the CHoCH consumption BOS reads). Two ways an old snapshot
can reach the new code, and what must happen in each:

1. Under the old algo version: never read, because the state key carries the
   version. The new version finds nothing and walks the window fresh.
2. Under the new version but without the new fields (a hand-written or
   half-migrated payload): not resumed. The walk starts fresh rather than
   crash or resume half a state.

And the structure engine reading a shift snapshot without the consumption
field reads it as "no CHoCH consumed", with its trend path intact.
"""

from __future__ import annotations

import json
from datetime import datetime

from tests.golden.harness.memory import InMemoryEngineStateStore
from tests.unit.application.detection.test_choch_bundle_d import _bullish_swings, _walk_series
from tests.unit.application.detection.test_structure_shift_replay import (
    FakeCandleRepository,
    FakeClock,
    FakeEventRepository,
    FakeEvidenceRepository,
)

import scanner.application.detection.structure_shift_replay as shift_module
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    StructureShiftReplayService,
    choch_consumption,
    trend_after,
)
from scanner.shared import Timeframe

_OLD_VERSION = "s6-structure-shift-v5"

# A v5-era detail: every field the old reader knew, none of the new ones.
_OLD_FIELDS = (
    "candidate",
    "consumed_choch",
    "floor_at",
    "mss_watch",
    "swings",
    "trend_path",
)


def _old_detail(series, last: int) -> str:
    return json.dumps(
        {
            "candidate": None,
            "consumed_choch": [],
            "floor_at": None,
            "mss_watch": None,
            "swings": [],
            "trend_path": [[series[0].open_time.isoformat(), "RANGING"]],
        }
    )


async def _run(monkeypatch, series, *, seed: StructureEngineState | None):
    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: _bullish_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())
    states = EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE)

    if seed is not None:
        await states.save(seed)

    events = FakeEventRepository()
    service = StructureShiftReplayService(
        FakeCandleRepository(series), events, FakeEvidenceRepository(()), FakeClock(), states
    )
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)
    saved = await states.load("BTCUSDT", Timeframe.H1.value, STRUCTURE_SHIFT_ALGO_VERSION)
    facts = sorted((e.event_type, e.event_at.isoformat()) for e in events.events.values())

    return facts, saved


async def test_a_snapshot_under_the_old_version_is_never_read(monkeypatch) -> None:
    series = _walk_series("94")
    fresh, _ = await _run(monkeypatch, series, seed=None)

    # An old-version snapshot claiming everything up to candle 25 is decided.
    seeded, saved = await _run(
        monkeypatch,
        series,
        seed=StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=_OLD_VERSION,
            last_processed_open_time=series[25].open_time.isoformat(),
            trend_state="RANGING",
            detail=_old_detail(series, 25),
        ),
    )

    assert seeded == fresh
    assert fresh  # the fixture does record its CHoCH, so equality is not vacuous
    assert saved is not None and saved.detail is not None
    assert {"leg", "turning", "leg_history", "surpassed", "choch_consumed"} <= set(
        json.loads(saved.detail)
    )


async def test_a_new_version_snapshot_without_the_new_fields_walks_fresh(monkeypatch) -> None:
    series = _walk_series("94")
    fresh, _ = await _run(monkeypatch, series, seed=None)

    seeded, saved = await _run(
        monkeypatch,
        series,
        seed=StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
            # Claims candles up to 25 decided, in the old format: resumed, the
            # walk would skip candle 20's CHoCH entirely.
            last_processed_open_time=series[25].open_time.isoformat(),
            trend_state="RANGING",
            detail=_old_detail(series, 25),
        ),
    )

    assert seeded == fresh
    assert saved is not None and saved.detail is not None
    assert "leg" in json.loads(saved.detail)


def test_structure_reads_an_old_shift_snapshot_as_no_choch_consumed() -> None:
    series = _walk_series("94")
    old = _old_detail(series, 25)

    assert choch_consumption(old) == {}
    assert choch_consumption(None) == {}
    assert choch_consumption("not json") == {}
    assert trend_after(old, series[3].open_time) is not None


def test_the_old_fields_are_still_written() -> None:
    """The new detail is a superset: nothing the old reader needed went away."""
    series = _walk_series("94")
    detail = json.loads(
        shift_module._snapshot(
            series,
            consumed_choch=set(),
            surpassed=set(),
            swings=[],
            candidate=None,
            mss_watch=None,
            floor_index=-1,
            trend_path=[(series[0].open_time, shift_module.TrendState.RANGING)],
        )
    )

    assert set(_OLD_FIELDS) <= set(detail)
    assert isinstance(datetime.fromisoformat(detail["trend_path"][0][0]), datetime)
