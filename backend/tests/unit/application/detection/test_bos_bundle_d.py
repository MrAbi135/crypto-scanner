"""SLS v1.0.20 §3.5: a BOS never breaks a swing a CHoCH consumed (option 1).

The structure engine reads the shift engine's snapshot from the previous
close. The fixture is `test_structure_resume`'s: the shift engine BULLISH
throughout, one external high at 11 before the window, and the newest candle
closing at 12.5 through it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from tests.support.builders import pad_for_warmup
from tests.unit.application.detection.test_choch_bundle_d import _bullish_swings, _walk_series
from tests.unit.application.detection.test_structure_replay import (
    FakeCandleRepository,
    FakeClock,
    FakeEventRepository,
    FakeStateStore,
    make_candle,
)

import scanner.application.detection.structure_shift_replay as shift_module
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_replay import (
    STRUCTURE_ALGO_VERSION,
    StructureReplayService,
)
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    choch_consumption,
)
from scanner.domain.structure import SwingKind
from scanner.shared import Timeframe


def _series():
    return pad_for_warmup(
        [
            *(make_candle(i, high="10", low="9") for i in range(5)),
            make_candle(5, high="13", low="12"),
        ]
    )


async def _breaks(candles, *, choch_consumed: list[list[str]] | None) -> list[tuple]:
    events = FakeEventRepository()
    states = EngineStateManager(FakeStateStore())
    shift = EngineStateManager(FakeStateStore(), namespace=SHIFT_NAMESPACE)
    level_at = candles[0].open_time - timedelta(hours=5)

    await states.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=STRUCTURE_ALGO_VERSION,
            last_processed_open_time=candles[-2].open_time.isoformat(),
            detail=json.dumps(
                {
                    "consumed": [],
                    "watches": [],
                    "breaks": [],
                    "swings": [[level_at.isoformat(), "11", "HIGH", "EXTERNAL"]],
                }
            ),
        )
    )

    detail = {"trend_path": [[candles[0].open_time.isoformat(), "BULLISH"]]}

    if choch_consumed is not None:
        detail["choch_consumed"] = choch_consumed

    await shift.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
            last_processed_open_time=candles[-2].open_time.isoformat(),
            trend_state="BULLISH",
            detail=json.dumps(detail),
        )
    )

    await StructureReplayService(
        FakeCandleRepository(candles),
        events,
        states,
        FakeClock(),
        shift_state=shift,
        shift_algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
    ).run("BTCUSDT", Timeframe.H1, candles[0].open_time, candles[-1].open_time + timedelta(hours=1))

    return [
        (e.event_type, e.event_at, json.loads(e.payload)["swing_price"])
        for e in events.events.values()
        if e.event_type.startswith("BOS_")
    ]


def _level(candles) -> str:
    return (candles[0].open_time - timedelta(hours=5)).isoformat()


async def test_a_bos_skips_a_swing_a_choch_consumed_on_an_earlier_candle() -> None:
    candles = _series()
    consumed = [[_level(candles), "HIGH", candles[-3].open_time.isoformat()]]

    assert await _breaks(candles, choch_consumed=consumed) == []


async def test_a_choch_on_the_break_candle_itself_does_not_block_the_bos() -> None:
    """Only an *earlier* CHoCH took the swing; one recorded on this very
    candle had not, when the candle closed."""
    candles = _series()
    consumed = [[_level(candles), "HIGH", candles[-1].open_time.isoformat()]]

    assert await _breaks(candles, choch_consumed=consumed) == [
        ("BOS_UP", candles[-1].open_time, "11")
    ]


async def test_a_choch_on_another_swing_does_not_block_the_bos() -> None:
    candles = _series()
    other = (candles[0].open_time - timedelta(hours=7)).isoformat()
    consumed = [[other, "HIGH", candles[-3].open_time.isoformat()]]

    assert await _breaks(candles, choch_consumed=consumed) == [
        ("BOS_UP", candles[-1].open_time, "11")
    ]


async def test_a_choch_on_the_low_at_the_same_time_does_not_block_the_high() -> None:
    candles = _series()
    consumed = [[_level(candles), "LOW", candles[-3].open_time.isoformat()]]

    assert await _breaks(candles, choch_consumed=consumed) == [
        ("BOS_UP", candles[-1].open_time, "11")
    ]


async def test_an_old_shift_snapshot_without_the_field_blocks_nothing() -> None:
    candles = _series()

    assert await _breaks(candles, choch_consumed=None) == [("BOS_UP", candles[-1].open_time, "11")]


# ---------- what the shift engine records ----------


async def _shift_run(monkeypatch, series, internal=()) -> tuple[dict, list[dict]]:
    from tests.unit.application.detection.test_structure_shift_replay import _service

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: _bullish_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: internal)
    service, events = _service(series, ())
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)
    saved = await service._state.load("BTCUSDT", Timeframe.H1.value, service._algo_version)
    assert saved is not None and saved.detail is not None
    chochs = [
        json.loads(e.payload) for e in events.events.values() if e.event_type.startswith("CHOCH")
    ]
    return json.loads(saved.detail), chochs


async def test_the_shift_engine_records_an_external_choch_with_its_candle(monkeypatch) -> None:
    series = _walk_series("94")

    detail, chochs = await _shift_run(monkeypatch, series)

    assert [c["swing_strength"] for c in chochs] == ["EXTERNAL"]
    assert detail["choch_consumed"] == [
        [series[8].open_time.isoformat(), "LOW", series[20].open_time.isoformat()]
    ]
    assert choch_consumption(json.dumps(detail)) == {
        (series[8].open_time, SwingKind.LOW): series[20].open_time
    }


async def test_an_internal_choch_is_not_recorded_for_bos(monkeypatch) -> None:
    """Only external consumption blocks a BOS. The leg (candle 11's dip, candle
    12's new high) holds no external higher low, so candle 20 breaks the
    internal low at 13; that consumption is not handed to BOS."""
    from tests.unit.application.detection.test_choch_bundle_d import _leg_window

    from scanner.domain.structure import SwingPoint, SwingStrength

    series = _leg_window()
    # 90 at 10 seeds the internal lows, so the 95 at 13 is an internal HL.
    internal = tuple(
        SwingPoint(i, series[i].open_time, Decimal(p), SwingKind.LOW, SwingStrength.INTERNAL)
        for i, p in ((10, "90"), (13, "95"))
    )

    detail, chochs = await _shift_run(monkeypatch, series, internal)

    assert [c["swing_strength"] for c in chochs] == ["INTERNAL"]
    assert detail["choch_consumed"] == []
