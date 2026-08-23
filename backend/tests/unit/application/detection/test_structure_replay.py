"""Tests for deterministic structure history replay."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.support.builders import pad_for_warmup

from scanner.application.detection.state import (
    EngineStateManager,
)
from scanner.application.detection.structure_replay import (
    StructureReplayService,
)
from scanner.application.ports.detection import (
    EngineEventRecord,
)
from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    SwingStrength,
)
from scanner.shared import Timeframe


class FakeClock:
    def now(self) -> datetime:
        return datetime(
            2026,
            8,
            10,
            12,
            tzinfo=UTC,
        )


class FakeCandleRepository:
    def __init__(
        self,
        candles: list[Candle],
    ) -> None:
        self.candles = candles

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        return [
            candle
            for candle in self.candles
            if (
                candle.symbol == symbol
                and candle.timeframe is timeframe
                and start <= candle.open_time < end
            )
        ]


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: dict[
            str,
            EngineEventRecord,
        ] = {}

    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool:
        if event.event_key in self.events:
            return False

        self.events[event.event_key] = event

        return True

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        return event_key in self.events


class FakeStateStore:
    def __init__(self) -> None:
        self.values: dict[
            str,
            str,
        ] = {}

    async def load(
        self,
        context_key: str,
    ) -> str | None:
        return self.values.get(context_key)

    async def save(
        self,
        context_key: str,
        payload: str,
    ) -> None:
        self.values[context_key] = payload

    async def delete(
        self,
        context_key: str,
    ) -> None:
        self.values.pop(
            context_key,
            None,
        )


def make_candle(
    index: int,
    *,
    high: str,
    low: str,
) -> Candle:
    high_value = Decimal(high)
    low_value = Decimal(low)

    midpoint = (high_value + low_value) / Decimal("2")

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        )
        + timedelta(hours=index),
        open=midpoint,
        high=high_value,
        low=low_value,
        close=midpoint,
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def sample_candles() -> list[Candle]:
    highs = [
        "10",
        "12",
        "18",
        "14",
        "13",
        "16",
        "12",
        "11",
        "20",
        "15",
        "14",
        "17",
        "13",
        "12",
        "22",
        "16",
        "15",
    ]

    lows = [
        "5",
        "6",
        "7",
        "6",
        "4",
        "7",
        "5",
        "3",
        "8",
        "6",
        "5",
        "8",
        "6",
        "4",
        "9",
        "7",
        "6",
    ]

    return [
        make_candle(
            index,
            high=high,
            low=low,
        )
        for index, (
            high,
            low,
        ) in enumerate(
            zip(
                highs,
                lows,
                strict=True,
            )
        )
    ]


@pytest.mark.asyncio
async def test_replay_persists_structure_events_and_state() -> None:
    events = FakeEventRepository()
    states = EngineStateManager(FakeStateStore())

    service = StructureReplayService(
        FakeCandleRepository(pad_for_warmup(sample_candles())),
        events,
        states,
        FakeClock(),
    )

    report = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        datetime(
            2026,
            8,
            3,
            tzinfo=UTC,
        ),
    )

    assert report.candles == 300
    assert report.internal_swings > 0
    assert report.events_inserted > 0

    state = await states.load(
        "BTCUSDT",
        Timeframe.H1.value,
        "s4-v7",
    )

    assert state is not None

    assert state.last_processed_open_time == sample_candles()[-1].open_time.isoformat()


@pytest.mark.asyncio
async def test_replay_is_idempotent() -> None:
    events = FakeEventRepository()

    service = StructureReplayService(
        FakeCandleRepository(pad_for_warmup(sample_candles())),
        events,
        EngineStateManager(FakeStateStore()),
        FakeClock(),
    )

    start = datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    )

    end = datetime(
        2026,
        8,
        3,
        tzinfo=UTC,
    )

    first = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        start,
        end,
    )

    second = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        start,
        end,
    )

    assert first.events_inserted > 0
    assert second.events_inserted == 0


@pytest.mark.asyncio
async def test_rebuild_state_replaces_old_snapshot() -> None:
    store = FakeStateStore()
    states = EngineStateManager(store)

    await store.save(
        (f"structure:s4-v7:BTCUSDT:{Timeframe.H1.value}"),
        (
            '{"algo_version":"s4-v7",'
            '"last_processed_open_time":null,'
            '"symbol":"BTCUSDT",'
            f'"timeframe":"{Timeframe.H1.value}",'
            '"trend_state":"BEARISH"}'
        ),
    )

    service = StructureReplayService(
        FakeCandleRepository(pad_for_warmup(sample_candles())),
        FakeEventRepository(),
        states,
        FakeClock(),
    )

    await service.run(
        "BTCUSDT",
        Timeframe.H1,
        datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        datetime(
            2026,
            8,
            3,
            tzinfo=UTC,
        ),
        rebuild_state=True,
    )

    state = await states.load(
        "BTCUSDT",
        Timeframe.H1.value,
        "s4-v7",
    )

    assert state is not None
    assert state.last_processed_open_time is not None


@pytest.mark.asyncio
async def test_empty_history_produces_empty_state() -> None:
    states = EngineStateManager(FakeStateStore())

    service = StructureReplayService(
        FakeCandleRepository([]),
        FakeEventRepository(),
        states,
        FakeClock(),
    )

    report = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        datetime(
            2026,
            8,
            2,
            tzinfo=UTC,
        ),
    )

    assert report.candles == 0
    assert report.events_inserted == 0
    assert report.trend_state == "RANGING"


@pytest.mark.asyncio
async def test_invalid_range_is_rejected() -> None:
    service = StructureReplayService(
        FakeCandleRepository([]),
        FakeEventRepository(),
        EngineStateManager(FakeStateStore()),
        FakeClock(),
    )

    point = datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    )

    with pytest.raises(
        ValueError,
        match="end must be greater than start",
    ):
        await service.run(
            "BTCUSDT",
            Timeframe.H1,
            point,
            point,
        )


@pytest.mark.asyncio
async def test_bos_replay_waits_for_confirmed_external_swings() -> None:
    candles: list[Candle] = []

    for index in range(40):
        close = Decimal("105")

        if index == 36:
            close = Decimal("125")

        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.H1,
                open_time=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=index),
                open=close,
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
                taker_buy_volume=Decimal("50"),
                trade_count=10,
                source=CandleSource.BACKFILL,
            )
        )

    external_swings = (
        SwingPoint(5, candles[5].open_time, Decimal("100"), SwingKind.HIGH, SwingStrength.EXTERNAL),
        SwingPoint(10, candles[10].open_time, Decimal("80"), SwingKind.LOW, SwingStrength.EXTERNAL),
        SwingPoint(
            15, candles[15].open_time, Decimal("110"), SwingKind.HIGH, SwingStrength.EXTERNAL
        ),
        SwingPoint(20, candles[20].open_time, Decimal("90"), SwingKind.LOW, SwingStrength.EXTERNAL),
        SwingPoint(
            25, candles[25].open_time, Decimal("120"), SwingKind.HIGH, SwingStrength.EXTERNAL
        ),
        SwingPoint(
            30, candles[30].open_time, Decimal("100"), SwingKind.LOW, SwingStrength.EXTERNAL
        ),
    )

    events = FakeEventRepository()

    service = StructureReplayService(
        FakeCandleRepository(candles),
        events,
        EngineStateManager(FakeStateStore()),
        FakeClock(),
    )

    inserted = await service._replay_bos(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=external_swings,
    )

    bos_events = [event for event in events.events.values() if event.event_type == "BOS_UP"]

    failed = [
        event for event in events.events.values() if event.event_type == "STRUCTURE_FAILED_BREAK_UP"
    ]

    # This fixture is the textbook failed break and always was: one candle
    # closes at 125 through the 120 level and every candle after it closes
    # back at 105. §3.5 records that as a fact, so the pass now inserts two.
    assert inserted == 2
    assert len(bos_events) == 1
    assert bos_events[0].event_at == candles[36].open_time

    assert len(failed) == 1
    assert failed[0].event_at == candles[37].open_time

    payload = json.loads(failed[0].payload)

    assert payload["failed"] is True
    assert payload["broken_level"] == "120"
    assert payload["elapsed_candles"] == 1
