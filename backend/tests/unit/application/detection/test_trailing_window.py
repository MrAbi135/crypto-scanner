"""Trailing-window runner: the arithmetic that decides what detection sees."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scanner.application.detection.trailing_window import (
    DEFAULT_WINDOW_CANDLES,
    TrailingWindowRunner,
)
from scanner.domain.common.warmup import DETECTION_MIN_CANDLES
from scanner.shared import Timeframe

CLOSE = datetime(2026, 8, 17, 12, tzinfo=UTC)


class RecordingPipeline:
    def __init__(self) -> None:
        self.windows: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def run(self, symbol, timeframe, start, end, *, rebuild_state=False):
        self.windows.append((symbol, timeframe, start, end))

        class _Report:
            structure = type("S", (), {"candles": 0, "events_inserted": 0})()
            structure_shift = type("T", (), {"trend_state": "RANGING"})()
            liquidity = type("L", (), {"pools_upserted": 0, "sweeps": 0})()

        return _Report()


@pytest.mark.asyncio
async def test_the_window_ends_past_the_candle_that_triggered_it() -> None:
    """`end` is exclusive, so it must clear the close's own open_time.

    Off by one step and the pass cannot see the very candle it was woken for --
    detection would run on every close and always be one bar behind, which is
    the kind of error that produces plausible output forever.
    """
    pipeline = RecordingPipeline()

    await TrailingWindowRunner(pipeline).run("BTCUSDT", Timeframe.H1, CLOSE)

    _, _, start, end = pipeline.windows[0]

    assert end == CLOSE + Timeframe.H1.duration
    assert start == CLOSE - Timeframe.H1.duration * DEFAULT_WINDOW_CANDLES


@pytest.mark.asyncio
async def test_the_window_scales_with_the_timeframe() -> None:
    pipeline = RecordingPipeline()

    runner = TrailingWindowRunner(pipeline, window_candles=400)

    await runner.run("BTCUSDT", Timeframe.H4, CLOSE)

    _, _, start, end = pipeline.windows[0]

    assert end - start == Timeframe.H4.duration * 401


def test_a_window_that_cannot_clear_the_warm_up_gate_is_refused() -> None:
    """SLS §1.9 needs 300 closed candles before detection is allowed.

    A shorter window is the worst kind of misconfiguration: the engine runs
    forever, finds itself cold on every pass, emits nothing, and reports no
    error at all. Refused at construction rather than discovered in a week of
    empty output.
    """
    for window in (0, 100, DETECTION_MIN_CANDLES):
        with pytest.raises(ValueError, match="warm-up minimum"):
            TrailingWindowRunner(RecordingPipeline(), window_candles=window)


def test_the_default_window_clears_the_gate_with_room() -> None:
    assert DEFAULT_WINDOW_CANDLES > DETECTION_MIN_CANDLES
