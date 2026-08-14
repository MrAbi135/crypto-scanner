from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scanner.application.detection.orchestrator import (
    DetectionOrchestrator,
    build_event_key,
)
from scanner.application.ports.detection import (
    EngineEventRecord,
)
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe


class FakeEventRepository:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool:
        if event.event_key in self.keys:
            return False

        self.keys.add(event.event_key)
        return True

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        return event_key in self.keys


def candle() -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=datetime(
            2026,
            8,
            10,
            tzinfo=UTC,
        ),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


async def test_repeated_processing_is_idempotent() -> None:
    repository = FakeEventRepository()

    async def detector(
        source: Candle,
    ) -> list[EngineEventRecord]:
        event_at = source.open_time

        return [
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=source.symbol,
                    timeframe=source.timeframe,
                    event_type="BOS_UP",
                    event_at=event_at,
                    algo_version="s4-v1",
                ),
                symbol=source.symbol,
                timeframe=source.timeframe,
                event_type="BOS_UP",
                event_at=event_at,
                algo_version="s4-v1",
                payload="{}",
                created_at=event_at,
            )
        ]

    orchestrator = DetectionOrchestrator(
        repository,
        [detector],
    )

    first = await orchestrator.process(candle())
    second = await orchestrator.process(candle())

    assert first == 1
    assert second == 0
    assert len(repository.keys) == 1


async def test_detector_order_is_sequential() -> None:
    calls: list[str] = []

    async def first(
        source: Candle,
    ) -> list[EngineEventRecord]:
        calls.append("first")
        return []

    async def second(
        source: Candle,
    ) -> list[EngineEventRecord]:
        calls.append("second")
        return []

    orchestrator = DetectionOrchestrator(
        FakeEventRepository(),
        [first, second],
    )

    await orchestrator.process(candle())

    assert calls == [
        "first",
        "second",
    ]


def test_event_key_is_deterministic() -> None:
    when = datetime(
        2026,
        8,
        10,
        tzinfo=UTC,
    )

    first = build_event_key(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        event_type="BOS_UP",
        event_at=when,
        algo_version="s4-v1",
    )

    second = build_event_key(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        event_type="BOS_UP",
        event_at=when,
        algo_version="s4-v1",
    )

    assert first == second
