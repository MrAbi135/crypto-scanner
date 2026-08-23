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
from scanner.domain.common import Candle, detection_is_warm
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
    failed_break_index,
    swing_window,
)
from scanner.shared import Timeframe

# s4-v2 (2026-08-17): first swing of each kind now emits an explicit SEED
# classification event. Output-changing, hence the increment — Constitution
# §44.5. Ratified as SLS v1.0.2 §3.3.
STRUCTURE_ALGO_VERSION = "s4-v5"


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
    warmup_satisfied: bool = True
    """False when SLS §1.9's closed-candle floor was not met.

    Reported rather than raised: §1.9 calls warm-up "visible, honest, not
    scored", so a caller must be able to tell a genuinely quiet market from a
    series the engine declined to analyse. Zero detections mean different
    things in those two cases.
    """


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

        if not detection_is_warm(len(candles)):
            state = StructureEngineState(
                symbol=symbol,
                timeframe=timeframe.value,
                algo_version=self._algo_version,
            )

            await self._states.save(state)

            return StructureReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                candles=len(candles),
                internal_swings=0,
                external_swings=0,
                classified_events=0,
                events_inserted=0,
                trend_state=state.trend_state,
                last_processed_open_time=(candles[-1].open_time if candles else None),
                warmup_satisfied=False,
            )

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        internal_classified = classify_swings(internal_swings)
        external_classified = classify_swings(external_swings)

        inserted = 0

        # §3.1: "every external swing is by construction also an internal
        # swing; it is stored once with `strength = external`". A k=5 pivot is
        # necessarily a k=2 pivot, so it comes back out of both detectors, and
        # persisting both wrote the same pivot twice under contradictory
        # strengths -- 486 of the VM's 493 external swings had an internal
        # twin at the same index.
        #
        # The liquidity engine already skipped these when building pools, and
        # the order-block engine parses every SWING_* event into swing
        # evidence, so it was counting each external pivot twice. Nothing
        # depended on the duplicate; one consumer worked around it and the
        # other was quietly wrong.
        promoted = {(swing.index, swing.kind) for swing in external_swings}

        for swing in (
            *(s for s in internal_swings if (s.index, s.kind) not in promoted),
            *external_swings,
        ):
            if await self._persist_swing(
                symbol,
                timeframe,
                swing,
            ):
                inserted += 1

        # Classifications are *not* deduplicated, and that is deliberate.
        # §3.3 labels a swing "relative to the previous confirmed swing of the
        # same type (per strength class)", and the two classes disagree about
        # the same pivot far more often than one would guess: on the VM, 157
        # of 523 pivots carrying both labels carry *different* ones -- an
        # external LH is frequently an internal HH, because its predecessor in
        # each sequence is a different swing.
        #
        # Dropping one would therefore delete a fact the doctrine computes,
        # not a duplicate. §3.1's "stored once" is a rule about the swing;
        # whether one swing may hold two labels is a §3.3 question and has
        # been raised as one rather than settled here.
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

            # ...and every older level of the same kind this close already
            # cleared, silently.
            #
            # §3.5 says "the break candle is the first closing candle beyond
            # the level". Only the most recent unconsumed level is a
            # candidate, so consuming one exposed the next one down -- and
            # price was usually far above it already, having closed through it
            # candles or hours earlier while the trend gate was shut. The
            # engine then recorded that as a break *here*, and did it again on
            # the following candle, marching backwards through history one
            # event per candle.
            #
            # Measured on the VM before the fix: 93 of 186 BOS events were
            # immediately followed, on the very next candle, by another break
            # of an older and lower level in the same direction. Half of every
            # recorded break was made by the queue rather than by price -- and
            # §8's F1 reads `BOS_{direction}` straight out of the window.
            #
            # A level under the close has been surpassed; it is bookkeeping,
            # not a structural event, so it is consumed without an event.
            for other in candidates:
                if other.index == swing.index:
                    continue

                surpassed = (
                    other.price < candle.close
                    if direction is BreakDirection.UP
                    else other.price > candle.close
                )

                if surpassed:
                    consumed.add((other.index, other.kind))

            if await self._persist_bos(
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
                candle_index=candle_index,
                swing=swing,
                direction=direction,
            ):
                inserted += 1

            # §3.5: "a failed break is recorded (fact, not deletion) if within
            # `failed_break_candles = 3` closed candles price closes back
            # beyond the broken level in the opposite direction". The BOS
            # stands either way -- this is a second fact about it, which is
            # why it is appended rather than used to withdraw the first.
            failed_at = failed_break_index(
                candles,
                break_index=candle_index,
                level=swing.price,
                direction=direction,
            )

            if failed_at is not None and await self._persist_failed_break(
                symbol=symbol,
                timeframe=timeframe,
                candle=candles[failed_at],
                candle_index=failed_at,
                break_index=candle_index,
                swing=swing,
                direction=direction,
            ):
                inserted += 1

        return inserted

    async def _persist_failed_break(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        break_index: int,
        swing: SwingPoint,
        direction: BreakDirection,
    ) -> bool:
        """§3.5's failed break, as its own fact.

        Not `BOS_FAILED_*`: two call sites already match on the `BOS_` prefix
        -- confluence's `_bos_break_indices` and the BOS replay tests -- and a
        failure is not a break.
        """
        event_type = f"STRUCTURE_FAILED_BREAK_{direction.value}"

        payload = json.dumps(
            {
                "direction": direction.value,
                "swing_index": swing.index,
                "broken_level": str(swing.price),
                "break_index": break_index,
                "failed_index": candle_index,
                "elapsed_candles": candle_index - break_index,
                "candle_close": str(candle.close),
                # §3.5: "downstream consumers (confluence, lifecycle) treat
                # `failed: true` as strong contrary evidence".
                "failed": True,
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
