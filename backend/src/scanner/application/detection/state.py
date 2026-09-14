"""Detection engine snapshot management."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from scanner.application.ports.detection import (
    EngineStateStore,
)
from scanner.domain.common import Candle
from scanner.shared import Timeframe

# Two engines keep a per-context snapshot and they are not the same quantity:
# §3.4's trend inferred from external swing labels (structure), and §3.7's
# TrendStateMachine moved by CHoCH and MSS (shift). Sharing one key would give
# a single field two writers, and whichever ran last would win silently.
STRUCTURE_NAMESPACE = "structure"
SHIFT_NAMESPACE = "shift"
# The last candle each engine decided (audit M3): a pass creates facts only
# about candles after it. One namespace per engine, so no two share a marker.
PARTICIPATION_NAMESPACE = "participation"
ICT_NAMESPACE = "ict"
ICT_OTE_NAMESPACE = "ict_ote"
ICT_OB_NAMESPACE = "ict_ob"
LIQUIDITY_NAMESPACE = "liquidity"


async def first_undecided_index(
    state: EngineStateManager | None,
    symbol: str,
    timeframe: Timeframe,
    algo_version: str,
    candles: Sequence[Candle],
) -> int:
    """The first candle of this window no earlier pass has decided (audit M3).

    A candle a pass decided while it was newest keeps that answer: deciding it
    again later only measures it against ATR seeded at a later window start.
    With no record -- no manager wired (the golden harness, `engine run` over a
    historical range), a version's first pass, or a last decided candle outside
    this window -- the whole window is undecided, as every pass used to treat it.
    """
    if state is None:
        return 0

    saved = await state.load(symbol, timeframe.value, algo_version)

    if saved is None or saved.last_processed_open_time is None:
        return 0

    decided = datetime.fromisoformat(saved.last_processed_open_time)

    for index, candle in enumerate(candles):
        if candle.open_time == decided:
            return index + 1

    return 0


async def mark_decided(
    state: EngineStateManager | None,
    symbol: str,
    timeframe: Timeframe,
    algo_version: str,
    candles: Sequence[Candle],
) -> None:
    """Record this window's newest candle as decided."""
    if state is None or not candles:
        return

    await state.save(
        StructureEngineState(
            symbol=symbol,
            timeframe=timeframe.value,
            algo_version=algo_version,
            last_processed_open_time=candles[-1].open_time.isoformat(),
        )
    )


@dataclass(frozen=True, slots=True)
class StructureEngineState:
    symbol: str
    timeframe: str
    algo_version: str
    last_processed_open_time: str | None = None
    trend_state: str = "RANGING"
    # The shift engine's walk state as JSON text (consumed CHoCH swings, the
    # MSS candidate and watch, the demotion floor, the trend path), keyed by
    # candle time so the next pass can resume instead of restarting (audit M6).
    # None for structure's own snapshot and for payloads written before it.
    detail: str | None = None


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
            detail=data.get("detail"),
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
