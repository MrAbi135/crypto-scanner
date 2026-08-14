from __future__ import annotations

from scanner.application.detection.state import (
    EngineStateManager,
    StructureEngineState,
)


class FakeStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

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


async def test_state_round_trip() -> None:
    store = FakeStore()
    manager = EngineStateManager(store)

    state = StructureEngineState(
        symbol="BTCUSDT",
        timeframe="1h",
        algo_version="s4-v1",
        last_processed_open_time=("2026-08-10T00:00:00+00:00"),
        trend_state="BULLISH",
    )

    await manager.save(state)

    loaded = await manager.load(
        "BTCUSDT",
        "1h",
        "s4-v1",
    )

    assert loaded == state


async def test_rebuild_discards_old_snapshot() -> None:
    store = FakeStore()
    manager = EngineStateManager(store)

    await manager.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe="1h",
            algo_version="s4-v1",
            trend_state="BEARISH",
        )
    )

    rebuilt = await manager.rebuild(
        "BTCUSDT",
        "1h",
        "s4-v1",
    )

    assert rebuilt.trend_state == "RANGING"

    assert (
        await manager.load(
            "BTCUSDT",
            "1h",
            "s4-v1",
        )
        == rebuilt
    )


async def test_missing_snapshot_returns_none() -> None:
    manager = EngineStateManager(FakeStore())

    assert (
        await manager.load(
            "ETHUSDT",
            "4h",
            "s4-v1",
        )
        is None
    )
