"""Detection engine snapshot management."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from scanner.application.ports.detection import (
    EngineStateStore,
)

# Two engines keep a per-context snapshot and they are not the same quantity:
# §3.4's trend inferred from external swing labels (structure), and §3.7's
# TrendStateMachine moved by CHoCH and MSS (shift). Sharing one key would give
# a single field two writers, and whichever ran last would win silently.
STRUCTURE_NAMESPACE = "structure"
SHIFT_NAMESPACE = "shift"


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
        *,
        namespace: str = STRUCTURE_NAMESPACE,
    ) -> None:
        self._store = store
        self._namespace = namespace

    def context_key(
        self,
        symbol: str,
        timeframe: str,
        algo_version: str,
    ) -> str:
        return f"{self._namespace}:{algo_version}:{symbol}:{timeframe}"

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
            algo_version=str(data["algo_version"]),
            last_processed_open_time=data.get("last_processed_open_time"),
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
