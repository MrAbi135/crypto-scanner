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
from scanner.domain.common import Candle
from scanner.domain.structure import (
    BreakDirection,
    ClassifiedSwing,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    classify_swings,
    detect_bos,
    detect_external_swings,
    detect_internal_swings,
    swing_window,
)
from scanner.shared import Timeframe

# s4-v2 (2026-08-17): first swing of each kind now emits an explicit SEED
# classification event. Output-changing, hence the increment — Constitution
# §44.5. Ratified as SLS v1.0.2 §3.3.
STRUCTURE_ALGO_VERSION = "s4-v2"


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
            raise ValueError("end must be greater than start")

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

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        internal_classified = classify_swings(internal_swings)
        external_classified = classify_swings(external_swings)

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

        bos_inserted = await self._replay_bos(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            external_swings=external_swings,
        )
        inserted += bos_inserted

        trend_state = _infer_external_trend(external_classified)

        last_open_time = candles[-1].open_time

        state = StructureEngineState(
            symbol=symbol,
            timeframe=timeframe.value,
            algo_version=self._algo_version,
            last_processed_open_time=(last_open_time.isoformat()),
            trend_state=trend_state,
        )

        await self._states.save(state)

        return StructureReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=len(candles),
            internal_swings=len(internal_swings),
            external_swings=len(external_swings),
            classified_events=(len(internal_classified) + len(external_classified)),
            events_inserted=inserted,
            trend_state=trend_state,
            last_processed_open_time=last_open_time,
        )

    async def _replay_bos(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candles: list[Candle],
        external_swings: tuple[SwingPoint, ...],
    ) -> int:
        """Replay BOS chronologically using trend known at each candle."""

        inserted = 0
        consumed: set[tuple[int, SwingKind]] = set()
        confirmation_window = swing_window(SwingStrength.EXTERNAL)

        for candle_index, candle in enumerate(candles):
            confirmed_swings = tuple(
                swing
                for swing in external_swings
                if swing.index + confirmation_window <= candle_index
            )

            if not confirmed_swings:
                continue

            classified = classify_swings(confirmed_swings)
            trend_state = _infer_external_trend(classified)

            if trend_state in {
                "BULLISH",
                "BULLISH_CAUTION",
            }:
                direction = BreakDirection.UP
                required_kind = SwingKind.HIGH

            elif trend_state in {
                "BEARISH",
                "BEARISH_CAUTION",
            }:
                direction = BreakDirection.DOWN
                required_kind = SwingKind.LOW

            else:
                continue

            candidates = [
                swing
                for swing in confirmed_swings
                if swing.kind is required_kind and (swing.index, swing.kind) not in consumed
            ]

            if not candidates:
                continue

            swing = max(
                candidates,
                key=lambda item: item.index,
            )

            bos = detect_bos(
                candle,
                swing,
                direction=direction,
            )

            if bos is None:
                continue

            consumed.add(
                (
                    swing.index,
                    swing.kind,
                )
            )

            if await self._persist_bos(
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
                candle_index=candle_index,
                swing=swing,
                direction=direction,
            ):
                inserted += 1

        return inserted

    async def _persist_bos(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        swing: SwingPoint,
        direction: BreakDirection,
    ) -> bool:
        event_type = f"BOS_{direction.value}"

        payload = json.dumps(
            {
                "direction": direction.value,
                "swing_index": swing.index,
                "swing_price": str(swing.price),
                "swing_kind": swing.kind.value,
                "swing_strength": swing.strength.value,
                "break_index": candle_index,
                "break_price": str(swing.price),
                "candle_close": str(candle.close),
                "consumed_by": event_type,
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
                    event_at=candle.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=candle.open_time,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    async def _persist_swing(
        self,
        symbol: str,
        timeframe: Timeframe,
        swing: SwingPoint,
    ) -> bool:
        event_type = f"SWING_{swing.strength.value}_{swing.kind.value}"

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

        event_type = f"STRUCTURE_{swing.strength.value}_{classified.label.value}"

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
