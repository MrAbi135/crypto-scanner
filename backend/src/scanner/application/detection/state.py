"""Detection engine snapshot management."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from scanner.application.ports.detection import (
    EngineStateStore,
)


@dataclass(frozen=True, slots=True)
class StructureEngineState:
    symbol: str
    timeframe: str
    algo_version: str
    last_processed_open_time: str | None = None
    trend_state: str = "RANGING"


class EngineStateManager:
    def __init__(
        self,
        store: EngineStateStore,
    ) -> None:
        self._store = store

    @staticmethod
    def context_key(
        symbol: str,
        timeframe: str,
        algo_version: str,
    ) -> str:
        return (
            f"structure:{algo_version}:"
            f"{symbol}:{timeframe}"
        )

    async def load(
        self,
        symbol: str,
        timeframe: str,
        algo_version: str,
    ) -> StructureEngineState | None:
        key = self.context_key(
            symbol,
            timeframe,
            algo_version,
        )

        raw = await self._store.load(key)

        if raw is None:
            return None

        data: dict[str, Any] = json.loads(raw)

        return StructureEngineState(
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            algo_version=str(
                data["algo_version"]
            ),
            last_processed_open_time=data.get(
                "last_processed_open_time"
            ),
            trend_state=str(
                data.get(
                    "trend_state",
                    "RANGING",
                )
            ),
        )

    async def save(
        self,
        state: StructureEngineState,
    ) -> None:
        key = self.context_key(
            state.symbol,
            state.timeframe,
            state.algo_version,
        )

        payload = json.dumps(
            asdict(state),
            sort_keys=True,
            separators=(",", ":"),
        )

        await self._store.save(
            key,
            payload,
        )

    async def rebuild(
        self,
        symbol: str,
        timeframe: str,
        algo_version: str,
    ) -> StructureEngineState:
        key = self.context_key(
            symbol,
            timeframe,
            algo_version,
        )

        await self._store.delete(key)

        state = StructureEngineState(
            symbol=symbol,
            timeframe=timeframe,
            algo_version=algo_version,
        )

        await self.save(state)

        return state
