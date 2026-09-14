"""The shift engine's snapshot: keyed by candle time, readable by old and new code."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore

from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_shift_replay import (
    _MssCandidate,
    _MssWatch,
    _resume,
    _snapshot,
    _stitch,
    trend_after,
)
from scanner.domain.common import Candle, CandleSource
from scanner.domain.structure import (
    BreakDirection,
    SwingKind,
    SwingPoint,
    SwingStrength,
    TrendState,
)
from scanner.shared import Timeframe

BASE = datetime(2026, 8, 1, tzinfo=UTC)


def series(start: int, count: int) -> list[Candle]:
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            open_time=BASE + timedelta(hours=start + i),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            taker_buy_volume=Decimal("1"),
            trade_count=1,
            source=CandleSource.BACKFILL,
        )
        for i in range(count)
    ]


def low(candles: list[Candle], index: int, price: str) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=candles[index].open_time,
        price=Decimal(price),
        kind=SwingKind.LOW,
        strength=SwingStrength.EXTERNAL,
    )


def test_the_snapshot_survives_the_window_sliding() -> None:
    """Saved against one window, restored into the next one: every piece of
    walk state lands on the same candle, at a different offset."""
    first = series(0, 50)
    detail = _snapshot(
        first,
        consumed_choch={(first[40].open_time, SwingStrength.EXTERNAL)},
        swings=[low(first, 3, "95"), low(first, 30, "96"), low(first, 47, "97")],
        candidate=_MssCandidate(
            direction=BreakDirection.DOWN,
            choch_index=47,
            swing_index=38,
            break_extreme=Decimal("98.5"),
            has_displacement=True,
            has_external_sweep=False,
            has_failure_swing=True,
            pre_mss_extreme=Decimal("101"),
        ),
        mss_watch=_MssWatch(
            direction=BreakDirection.UP,
            pre_mss_extreme=Decimal("97"),
            confirmed_index=45,
            event_key="k",
        ),
        floor_index=42,
        trend_path=[
            (first[10].open_time, TrendState.BULLISH),
            (first[47].open_time, TrendState.BULLISH_CAUTION),
        ],
    )
    state = StructureEngineState(
        symbol="BTCUSDT",
        timeframe="H1",
        algo_version="v",
        last_processed_open_time=first[-1].open_time.isoformat(),
        trend_state=TrendState.BULLISH_CAUTION.value,
        detail=detail,
    )

    # The next pass's window starts five candles later.
    second = series(5, 50)
    resumed = _resume(state, second)

    assert resumed is not None
    assert resumed.next_index == 45
    assert resumed.trend is TrendState.BULLISH_CAUTION
    assert resumed.consumed_choch == {(first[40].open_time, SwingStrength.EXTERNAL)}
    assert resumed.candidate is not None
    assert (resumed.candidate.choch_index, resumed.candidate.swing_index) == (42, 33)
    assert resumed.candidate.pre_mss_extreme == Decimal("101")
    assert resumed.mss_watch is not None and resumed.mss_watch.confirmed_index == 40
    assert resumed.floor_index == 37
    assert [state for _, state in resumed.trend_path] == [
        TrendState.BULLISH,
        TrendState.BULLISH_CAUTION,
    ]
    # The swing at candle 47 had not confirmed (k=5) when the snapshot was
    # taken, so it is not carried: the next window detects it itself.
    assert [swing.open_time for swing in resumed.swings] == [
        first[3].open_time,
        first[30].open_time,
    ]


def test_a_resumed_walk_takes_the_window_start_swings_from_the_snapshot() -> None:
    """A swing just inside the window may have lost its left side, and one just
    before it is gone; both come from the snapshot, re-addressed to this window."""
    window = series(20, 60)
    carried = [
        SwingPoint(
            0, BASE + timedelta(hours=12), Decimal("90"), SwingKind.LOW, SwingStrength.EXTERNAL
        ),
        SwingPoint(0, window[4].open_time, Decimal("88"), SwingKind.LOW, SwingStrength.EXTERNAL),
        SwingPoint(0, window[30].open_time, Decimal("80"), SwingKind.LOW, SwingStrength.EXTERNAL),
    ]
    detected = [low(window, 25, "85"), low(window, 30, "80")]

    stitched = _stitch(carried, detected, window, SwingStrength.EXTERNAL)

    assert [(swing.index, swing.price) for swing in stitched] == [
        (-8, Decimal("90")),
        (4, Decimal("88")),
        (25, Decimal("85")),
        (30, Decimal("80")),
    ]


def test_a_snapshot_whose_last_candle_left_the_window_is_not_resumed() -> None:
    """A gap longer than the window: nothing to resume from, so the pass walks
    the window from RANGING like a first pass."""
    first = series(0, 50)
    state = StructureEngineState(
        symbol="BTCUSDT",
        timeframe="H1",
        algo_version="v",
        last_processed_open_time=first[-1].open_time.isoformat(),
        trend_state="BULLISH",
        detail=_snapshot(
            first,
            consumed_choch=set(),
            swings=[],
            candidate=None,
            mss_watch=None,
            floor_index=-1,
            trend_path=[],
        ),
    )

    assert _resume(state, series(60, 50)) is None


def test_the_trend_path_answers_for_any_processed_candle() -> None:
    candles = series(0, 20)
    detail = _snapshot(
        candles,
        consumed_choch=set(),
        swings=[],
        candidate=None,
        mss_watch=None,
        floor_index=-1,
        trend_path=[
            (candles[0].open_time, TrendState.RANGING),
            (candles[5].open_time, TrendState.BULLISH),
            (candles[12].open_time, TrendState.BULLISH_CAUTION),
        ],
    )

    assert trend_after(detail, candles[4].open_time) is TrendState.RANGING
    assert trend_after(detail, candles[5].open_time) is TrendState.BULLISH
    assert trend_after(detail, candles[11].open_time) is TrendState.BULLISH
    assert trend_after(detail, candles[12].open_time) is TrendState.BULLISH_CAUTION
    assert trend_after(detail, candles[0].open_time - timedelta(hours=1)) is None
    assert trend_after(None, candles[4].open_time) is None


@pytest.mark.asyncio
async def test_a_pass_saves_the_trend_after_every_candle_it_walked() -> None:
    """Structure's break gate reads the trend in force at a candle from this
    path, so a pass must record it for every candle it walked -- not just the
    one it ended on."""
    from tests.golden.harness.memory import (
        InMemoryCandleRepository,
        InMemoryEngineEventRepository,
        InMemoryIctEvidenceRepository,
        InMemoryLiquidityTransitionRepository,
    )

    from scanner.application.detection.structure_shift_replay import (
        STRUCTURE_SHIFT_ALGO_VERSION,
        StructureShiftReplayService,
    )

    candles = series(0, 40)
    events = InMemoryEngineEventRepository()
    manager = EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE)

    class Clock:
        def now(self) -> datetime:
            return candles[-1].close_time

    report = await StructureShiftReplayService(
        InMemoryCandleRepository(candles),
        events,
        InMemoryIctEvidenceRepository(events, InMemoryLiquidityTransitionRepository()),
        Clock(),
        manager,
    ).run("BTCUSDT", Timeframe.H1, candles[0].open_time, candles[-1].open_time + timedelta(hours=1))

    saved = await manager.load("BTCUSDT", "H1", STRUCTURE_SHIFT_ALGO_VERSION)

    assert saved is not None
    assert trend_after(saved.detail, candles[0].open_time) is not None
    assert trend_after(saved.detail, candles[-1].open_time) is TrendState(report.trend_state)


@pytest.mark.asyncio
async def test_a_payload_written_before_the_detail_field_still_loads() -> None:
    """Readers that only want the trend -- structure's seed, confluence's HTF
    read -- must keep working on snapshots written by the previous version."""
    store = InMemoryEngineStateStore()
    manager = EngineStateManager(store, namespace=SHIFT_NAMESPACE)
    key = manager.context_key("BTCUSDT", "H1", "old")

    await store.save(
        key,
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "timeframe": "H1",
                "algo_version": "old",
                "last_processed_open_time": BASE.isoformat(),
                "trend_state": "BEARISH",
            }
        ),
    )

    loaded = await manager.load("BTCUSDT", "H1", "old")

    assert loaded is not None
    assert loaded.trend_state == "BEARISH"
    assert loaded.detail is None
    assert _resume(loaded, series(0, 10)) is None
