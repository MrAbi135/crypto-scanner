"""Ports owned by the S4 detection engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class EngineEventRecord:
    event_key: str
    symbol: str
    timeframe: Timeframe
    event_type: str
    event_at: datetime
    algo_version: str
    payload: str
    created_at: datetime


class EngineEventRepository(Protocol):
    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool: ...

    async def exists(
        self,
        event_key: str,
    ) -> bool: ...

    # Confluence (§8) needs every engine's output, not one engine's slice.
    # `IctEvidenceRepository.list_structure` deliberately filters to SWING_* and
    # STRUCTURE_*, which is right for the ICT engines that ask it for swing
    # context and wrong for a reader that must also see BOS, CHOCH, MSS, sweeps
    # and participation flags.
    async def list_events(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[EngineEventRecord, ...]: ...


class EngineStateStore(Protocol):
    async def load(
        self,
        context_key: str,
    ) -> str | None: ...

    async def save(
        self,
        context_key: str,
        payload: str,
    ) -> None: ...

    async def delete(
        self,
        context_key: str,
    ) -> None: ...
