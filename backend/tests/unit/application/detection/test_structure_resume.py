"""A resumed structure pass: what it reads back, and where it stops (s4-v10).

The close-by-close property covers the ordinary path. These pin two cases a
generated series never reaches: a break level the window start has already
cut off, and a candle the shift engine has not recorded a trend for yet.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from tests.support.builders import pad_for_warmup
from tests.unit.application.detection.test_structure_replay import (
    FakeCandleRepository,
    FakeClock,
    FakeEventRepository,
    FakeStateStore,
    make_candle,
)

from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_replay import (
    STRUCTURE_ALGO_VERSION,
    StructureReplayService,
)
from scanner.application.detection.structure_shift_replay import STRUCTURE_SHIFT_ALGO_VERSION
from scanner.shared import Timeframe


def _series():
    # Flat history (no swing of its own), then one candle closing at 12.5.
    return pad_for_warmup(
        [
            *(make_candle(i, high="10", low="9") for i in range(5)),
            make_candle(5, high="13", low="12"),
        ]
    )


async def _service(
    candles,
    *,
    structure_decided,
    shift_decided,
):
    events = FakeEventRepository()
    states = EngineStateManager(FakeStateStore())
    shift = EngineStateManager(FakeStateStore(), namespace=SHIFT_NAMESPACE)

    # A pass already decided the window up to `structure_decided`. The only
    # external high it knows of sits before this window, at 11.
    await states.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=STRUCTURE_ALGO_VERSION,
            last_processed_open_time=structure_decided.isoformat(),
            detail=json.dumps(
                {
                    "consumed": [],
                    "watches": [],
                    "breaks": [],
                    "swings": [
                        [
                            (candles[0].open_time - timedelta(hours=5)).isoformat(),
                            "11",
                            "HIGH",
                            "EXTERNAL",
                        ]
                    ],
                }
            ),
        )
    )

    # The shift engine has been BULLISH throughout, up to `shift_decided`.
    await shift.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
            last_processed_open_time=shift_decided.isoformat(),
            trend_state="BULLISH",
            detail=json.dumps({"trend_path": [[candles[0].open_time.isoformat(), "BULLISH"]]}),
        )
    )

    service = StructureReplayService(
        FakeCandleRepository(candles),
        events,
        states,
        FakeClock(),
        shift_state=shift,
        shift_algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
    )

    await service.run(
        "BTCUSDT",
        Timeframe.H1,
        candles[0].open_time,
        candles[-1].open_time + timedelta(hours=1),
    )

    return events, states


@pytest.mark.asyncio
async def test_a_level_from_before_the_window_is_still_a_break_level() -> None:
    """§3.5: the level is "the most recent unconsumed confirmed external swing
    high", and the window start does not consume anything. A resumed pass reads
    that level back from its snapshot; re-detecting only what the window shows,
    it would find no level at all and the close through 11 would break nothing."""
    candles = _series()

    events, _ = await _service(
        candles,
        structure_decided=candles[-2].open_time,
        shift_decided=candles[-2].open_time,
    )

    breaks = [e for e in events.events.values() if e.event_type.startswith("BOS_")]

    assert [(e.event_type, e.event_at, json.loads(e.payload)["swing_price"]) for e in breaks] == [
        ("BOS_UP", candles[-1].open_time, "11")
    ]


@pytest.mark.asyncio
async def test_a_pass_does_not_decide_a_candle_the_shift_engine_has_not_reached() -> None:
    """The shift engine runs after structure, so on a catch-up its path can end
    before this window's newest candle. That candle's trend is unknown, and a
    break decided now would be decided without it: the pass stops there and
    records how far it got, so the next pass decides it."""
    candles = _series()

    events, states = await _service(
        candles,
        structure_decided=candles[-3].open_time,
        shift_decided=candles[-3].open_time,
    )

    saved = await states.load("BTCUSDT", Timeframe.H1.value, STRUCTURE_ALGO_VERSION)

    assert saved is not None
    assert saved.last_processed_open_time == candles[-2].open_time.isoformat()
    assert not [e for e in events.events.values() if e.event_type.startswith("BOS_")]
