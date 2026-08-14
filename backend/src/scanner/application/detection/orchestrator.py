"""Idempotent detection orchestrator skeleton (Sprint S4)."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.domain.common import Candle
from scanner.shared import Timeframe

Detector = Callable[
    [Candle],
    Awaitable[Sequence[EngineEventRecord]],
]


def build_event_key(
    *,
    symbol: str,
    timeframe: Timeframe,
    event_type: str,
    event_at: datetime,
    algo_version: str,
) -> str:
    raw = "|".join(
        (
            symbol,
            timeframe.value,
            event_type,
            event_at.isoformat(),
            algo_version,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DetectionOrchestrator:
    """Sequential per-context detector execution.

    Detector order is deterministic. Persistence uniqueness makes repeated
    delivery idempotent.
    """

    def __init__(
        self,
        events: EngineEventRepository,
        detectors: Sequence[Detector],
    ) -> None:
        self._events = events
        self._detectors = tuple(detectors)

    async def process(
        self,
        candle: Candle,
    ) -> int:
        inserted = 0

        for detector in self._detectors:
            produced = await detector(candle)

            for event in produced:
                if await self._events.append(event):
                    inserted += 1

        return inserted
