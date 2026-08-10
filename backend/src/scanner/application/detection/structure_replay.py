"""Structure history replay and state rebuild service (Sprint S4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.detection.state import (
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.domain.structure import (
    ClassifiedSwing,
    StructureLabel,
    SwingPoint,
    classify_swings,
    detect_external_swings,
    detect_internal_swings,
)
from scanner.shared import Timeframe

STRUCTURE_ALGO_VERSION = "s4-v1"


@dataclass(frozen=True, slots=True)
class StructureReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    internal_swings: int
    external_swings: int
    classified_events: int
    events_inserted: int
    trend_state: str
    last_processed_open_time: datetime | None


class StructureReplayService:
    """Replay closed candle history into deterministic structure facts."""

    def __init__(
        self,
        candles: CandleRepository,
        events: EngineEventRepository,
        states: EngineStateManager,
        clock: Clock,
        *,
        algo_version: str = STRUCTURE_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._events = events
        self._states = states
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        rebuild_state: bool = False,
    ) -> StructureReplayReport:
        if end <= start:
            raise ValueError(
                "end must be greater than start"
            )

        if rebuild_state:
            await self._states.rebuild(
                symbol,
                timeframe.value,
                self._algo_version,
            )

        candles = list(
            await self._candles.fetch_series(
                symbol,
                timeframe,
                start,
                end,
            )
        )

        if not candles:
            state = StructureEngineState(
                symbol=symbol,
                timeframe=timeframe.value,
                algo_version=self._algo_version,
            )

            await self._states.save(state)

            return StructureReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                candles=0,
                internal_swings=0,
                external_swings=0,
                classified_events=0,
                events_inserted=0,
                trend_state=state.trend_state,
                last_processed_open_time=None,
            )

        internal_swings = detect_internal_swings(
            candles
        )
        external_swings = detect_external_swings(
            candles
        )

        internal_classified = classify_swings(
            internal_swings
        )
        external_classified = classify_swings(
            external_swings
        )

        inserted = 0

        for swing in (
            *internal_swings,
            *external_swings,
        ):
            if await self._persist_swing(
                symbol,
                timeframe,
                swing,
            ):
                inserted += 1

        for classified in (
            *internal_classified,
            *external_classified,
        ):
            if await self._persist_classification(
                symbol,
                timeframe,
                classified,
            ):
                inserted += 1

        trend_state = _infer_external_trend(
            external_classified
        )

        last_open_time = candles[-1].open_time

        state = StructureEngineState(
            symbol=symbol,
            timeframe=timeframe.value,
            algo_version=self._algo_version,
            last_processed_open_time=(
                last_open_time.isoformat()
            ),
            trend_state=trend_state,
        )

        await self._states.save(state)

        return StructureReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=len(candles),
            internal_swings=len(internal_swings),
            external_swings=len(external_swings),
            classified_events=(
                len(internal_classified)
                + len(external_classified)
            ),
            events_inserted=inserted,
            trend_state=trend_state,
            last_processed_open_time=last_open_time,
        )

    async def _persist_swing(
        self,
        symbol: str,
        timeframe: Timeframe,
        swing: SwingPoint,
    ) -> bool:
        event_type = (
            f"SWING_{swing.strength.value}_"
            f"{swing.kind.value}"
        )

        payload = json.dumps(
            {
                "index": swing.index,
                "price": str(swing.price),
                "kind": swing.kind.value,
                "strength": swing.strength.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=swing.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=swing.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    async def _persist_classification(
        self,
        symbol: str,
        timeframe: Timeframe,
        classified: ClassifiedSwing,
    ) -> bool:
        swing = classified.swing

        event_type = (
            f"STRUCTURE_{swing.strength.value}_"
            f"{classified.label.value}"
        )

        payload = json.dumps(
            {
                "index": swing.index,
                "price": str(swing.price),
                "kind": swing.kind.value,
                "strength": swing.strength.value,
                "label": classified.label.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        return await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=swing.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=swing.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )


def _infer_external_trend(
    classified: tuple[ClassifiedSwing, ...],
) -> str:
    """Require two consecutive structural pairs before assigning trend."""

    highs = [
        item.label
        for item in classified
        if item.label
        in {
            StructureLabel.HH,
            StructureLabel.LH,
            StructureLabel.EQH,
        }
    ]

    lows = [
        item.label
        for item in classified
        if item.label
        in {
            StructureLabel.HL,
            StructureLabel.LL,
            StructureLabel.EQL,
        }
    ]

    if (
        len(highs) >= 2
        and len(lows) >= 2
        and highs[-2:]
        == [
            StructureLabel.HH,
            StructureLabel.HH,
        ]
        and lows[-2:]
        == [
            StructureLabel.HL,
            StructureLabel.HL,
        ]
    ):
        return "BULLISH"

    if (
        len(highs) >= 2
        and len(lows) >= 2
        and highs[-2:]
        == [
            StructureLabel.LH,
            StructureLabel.LH,
        ]
        and lows[-2:]
        == [
            StructureLabel.LL,
            StructureLabel.LL,
        ]
    ):
        return "BEARISH"

    return "RANGING"
