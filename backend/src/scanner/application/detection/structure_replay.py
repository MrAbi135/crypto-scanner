"""Structure history replay and state rebuild service (Sprint S4)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.detection.state import (
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.detection.structure_events import classification_event_type
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.domain.common import (
    TOLERANCE_ATR,
    Candle,
    detection_is_warm,
    wilder_atr_series,
)
from scanner.domain.structure import (
    IDLE_CANDLES,
    BreakDirection,
    ClassifiedSwing,
    SwingKind,
    SwingPoint,
    SwingStrength,
    TrendState,
    TrendStateMachine,
    classify_swings,
    detect_bos,
    detect_external_swings,
    detect_internal_swings,
    failed_break_index,
    structure_is_idle,
    swing_window,
)
from scanner.shared import Timeframe

# s4-v2 (2026-08-17): first swing of each kind now emits an explicit SEED
# classification event. Output-changing, hence the increment — Constitution
# §44.5. Ratified as SLS v1.0.2 §3.3.
STRUCTURE_ALGO_VERSION = "s4-v8"


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
        shift_state: EngineStateManager | None = None,
        shift_algo_version: str | None = None,
    ) -> None:
        self._candles = candles
        self._events = events
        self._states = states
        self._clock = clock
        self._algo_version = algo_version
        # §3.4's maintained state, as the shift engine last left it. Optional
        # so the golden harness and `engine run` can drive this service alone;
        # absent, the window starts from RANGING, which is where a series with
        # no history starts anyway.
        self._shift_state = shift_state
        self._shift_algo_version = shift_algo_version

    async def _seed_trend(self, symbol: str, timeframe: Timeframe) -> TrendState:
        """The trend as the shift engine last recorded it, or RANGING.

        RANGING on absence rather than a guess: §3.4's diagram starts there,
        and a series whose state has never been written has never earned a
        trend.
        """
        if self._shift_state is None or self._shift_algo_version is None:
            return TrendState.RANGING

        state = await self._shift_state.load(
            symbol,
            timeframe.value,
            self._shift_algo_version,
        )

        if state is None:
            return TrendState.RANGING

        try:
            return TrendState(state.trend_state)
        except ValueError:
            # A value this enum does not know. Reported as RANGING rather than
            # raised: an unreadable state should cost breaks, not the pass.
            return TrendState.RANGING

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

        # §3.4: "Maintain one authoritative directional state". Seeded from
        # what the shift engine last persisted, then advanced candle by candle
        # inside the window.
        #
        # The shift engine runs *after* this one in the pipeline, so the state
        # read here is the previous close's. That is the causally correct
        # answer: at the moment a candle breaks a level, the prevailing trend
        # is the one established before it printed. Reading the current pass's
        # own conclusion would be reading the future.
        trend = TrendStateMachine(state=await self._seed_trend(symbol, timeframe))

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
        # the same pivot far more often than one would guess. Re-measured on
        # the VM 2026-08-23: 695 pivots carry both labels and **152 carry
        # different ones** -- LL against HL 80 times, HH against LH 59. No
        # pivot carries an external label without an internal one, which is
        # §3.1's nesting holding.
        #
        # Neither label is wrong. The internal sequence has more and closer
        # pivots, so a pullback low under the last internal low can still sit
        # above the last external one; the two answer different questions.
        # Dropping one would delete a fact the doctrine computes, not a
        # duplicate -- §3.1's "stored once" is a rule about the swing, not
        # about the label.
        #
        # Settled by the developer 2026-08-23: both are kept. The hazard that
        # comes with that is reading them by prefix, and it is closed in
        # `structure_events` -- nothing can ask for a label without naming
        # which of the two series it means.
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

        bos_inserted, broke_at = await self._replay_bos(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            external_swings=external_swings,
            trend=trend,
        )
        inserted += bos_inserted

        # The state the machine actually held after the window, not a fresh
        # re-derivation. Reporting one thing while the BOS gate acted on
        # another is how the engine came to log `trend: BULLISH` on a series
        # where no break had fired in eight days.
        trend_state = _idle_adjusted(
            trend.state.value,
            candles=candles,
            external_swings=external_swings,
            broke_at=broke_at,
        )

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
        trend: TrendStateMachine,
    ) -> tuple[int, frozenset[int]]:
        """Replay BOS chronologically using trend known at each candle.

        Returns the break indices as well as the count, because §3.4's
        idle rule needs to know whether the trend broke anything recently
        and the count cannot say when.
        """

        inserted = 0
        broke_at: set[int] = set()
        consumed: set[tuple[int, SwingKind]] = set()
        confirmation_window = swing_window(SwingStrength.EXTERNAL)

        # §3.5 edge case (2): "break of a level within e: not a break
        # (tolerance is absolute)", where §0.4 fixes e as
        # `P.global.tolerance_atr x ATR`. `detect_bos` takes the tolerance and
        # defaults it to zero, and this call site passed nothing -- so the
        # doctrine's own noise filter for breaks was the one place in the
        # codebase where e did not exist. The liquidity engine derives it the
        # same way for pools, clusters and sweeps.
        atrs = wilder_atr_series(candles)

        for candle_index, candle in enumerate(candles):
            confirmed_swings = tuple(
                swing
                for swing in external_swings
                if swing.index + confirmation_window <= candle_index
            )

            if not confirmed_swings:
                continue

            classified = classify_swings(confirmed_swings)

            # The entry edge, applied to the maintained state rather than
            # re-derived. `apply_structure` is a no-op once a trend is held --
            # §3.4 draws no edge from BULLISH back to RANGING except the idle
            # rule, and leaving a trend is the shift engine's job.
            trend_state = trend.apply_structure(classified).value

            if trend_state in {
                TrendState.BULLISH.value,
                TrendState.BULLISH_CAUTION.value,
            }:
                direction = BreakDirection.UP
                required_kind = SwingKind.HIGH

            elif trend_state in {
                TrendState.BEARISH.value,
                TrendState.BEARISH_CAUTION.value,
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

            if _is_outside_bar(
                candle,
                confirmed_swings,
                consumed,
            ) and not _close_agrees(candle, direction):
                # §3.5 edge case (1). See `_close_agrees` for why this only
                # withholds the break and does not record the opposite one.
                continue

            atr = atrs[candle_index] if candle_index < len(atrs) else None

            bos = detect_bos(
                candle,
                swing,
                direction=direction,
                # At the break candle, not at the swing's: the question is
                # whether this close is distinguishable from the level in
                # today's volatility.
                epsilon=TOLERANCE_ATR * atr if atr is not None else Decimal(0),
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
                broke_at.add(candle_index)

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

        return inserted, frozenset(broke_at)

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

        event_type = classification_event_type(swing.strength, classified.label)

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


def _is_outside_bar(
    candle: Candle,
    confirmed_swings: tuple[SwingPoint, ...],
    consumed: set[tuple[int, SwingKind]],
) -> bool:
    """§3.5 edge case (1)'s precondition: one candle takes out both extremes.

    "One candle closes beyond both an external high and low (extreme outside
    bar)" -- the candle's range engulfs an unconsumed external high and an
    unconsumed external low at once, so both are penetrated and only one of
    them can be the break.

    The candle's *range* is what has to reach both, not its close: a close is
    a single price and cannot sit above a high and below a lower low. Reading
    it as the close instead would need a low priced above a high, which is an
    inverted and near-unreachable structure, and would have made this rule
    fire on almost nothing.

    Only the trend's own kind is ever consumed -- a BULLISH pass consumes
    highs and never touches lows -- so the opposite side is read as it stands.
    """
    took_high = any(
        candle.high > swing.price
        for swing in confirmed_swings
        if swing.kind is SwingKind.HIGH and (swing.index, swing.kind) not in consumed
    )

    took_low = any(
        candle.low < swing.price
        for swing in confirmed_swings
        if swing.kind is SwingKind.LOW and (swing.index, swing.kind) not in consumed
    )

    return took_high and took_low


def _close_agrees(candle: Candle, direction: BreakDirection) -> bool:
    """§3.5 edge case (1)'s resolution: "bullish close => bullish BOS only".

    The candle's own body, not which side of the level it finished on -- an
    outside bar is beyond both levels by construction, so "which side" cannot
    resolve anything and only the body can.

    This withholds a break; it never records one. Edge case (1) would have the
    opposite penetration decide the direction, but §3.5's main rule requires
    "the trend agreeing", and a bearish BOS during a BULLISH trend cannot
    satisfy both clauses at once. The reading taken here is the one both can
    live with: the contrapositive of "bullish close => bullish BOS only" is
    that a bearish close yields no bullish BOS, and edge case (1) sends the
    other penetration to the Liquidity Engine as a sweep candidate anyway,
    which is where it is already handled. Settled by the developer
    2026-08-23; the alternative reading, which manufactures a
    counter-trend BOS, remains available if the doctrine is amended.

    A doji closes in neither direction and so agrees with neither. That is
    only reachable inside an outside bar, where the whole question is which
    way the candle resolved and a doji has not answered.
    """
    if direction is BreakDirection.UP:
        return candle.close > candle.open

    return candle.close < candle.open


def _idle_adjusted(
    trend_state: str,
    *,
    candles: Sequence[Candle],
    external_swings: Sequence[SwingPoint],
    broke_at: frozenset[int],
) -> str:
    """§3.4's `BULLISH --> RANGING: structure idle 100 candles`, and its mirror.

    The label sequence says what the trend *was*; this asks whether it has
    since gone quiet. A market that has closed inside its own external bracket
    for a hundred candles without breaking a level is not trending, however
    tidy the last pair of swings looked.

    The bracket is the most recent confirmed external swing on each side --
    §5.7's anchors, which §5.7 itself calls "confirmed-swing facts". With
    either side missing there is no bracket to be inside of, and the trend
    stands.

    Only BULLISH and BEARISH are eligible, because those are the two edges the
    state diagram draws. RANGING is already the destination and the CAUTION
    states are mid-transition -- idling out of one would discard the CHoCH
    that put it there.
    """
    if trend_state not in {TrendState.BULLISH.value, TrendState.BEARISH.value}:
        return trend_state

    highs = [s for s in external_swings if s.kind is SwingKind.HIGH]
    lows = [s for s in external_swings if s.kind is SwingKind.LOW]

    if not highs or not lows:
        return trend_state

    high = max(highs, key=lambda s: s.index)
    low = max(lows, key=lambda s: s.index)

    if high.price < low.price:
        return trend_state

    window_start = len(candles) - IDLE_CANDLES

    idle = structure_is_idle(
        [candle.close for candle in candles],
        range_low=low.price,
        range_high=high.price,
        broke_externally=any(index >= window_start for index in broke_at),
    )

    return TrendState.RANGING.value if idle else trend_state
