"""Chronological CHoCH/MSS replay using S4/S5 evidence."""

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
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
)
from scanner.domain.common import Candle, wilder_atr
from scanner.domain.ict import (
    DisplacementDirection,
    detect_displacement,
)
from scanner.domain.structure import (
    BreakDirection,
    ClassifiedSwing,
    MssEvidence,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    TrendState,
    TrendStateMachine,
    classify_swings,
    detect_external_swings,
    detect_internal_swings,
    evaluate_mss,
    swing_window,
)
from scanner.shared import Timeframe
from scanner.shared.errors import DomainInvariantError

STRUCTURE_SHIFT_ALGO_VERSION = "s6-structure-shift-v2"

_ATR_PERIOD = 14
_MSS_SWEEP_LOOKBACK = 10
_MSS_FOLLOWTHROUGH_MAX = 5
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class StructureShiftReplayReport:
    symbol: str
    timeframe: Timeframe
    choch_created: int
    mss_created: int
    failed_candidates: int
    events_inserted: int
    trend_state: str


@dataclass(slots=True)
class _MssCandidate:
    direction: BreakDirection
    choch_index: int
    swing_index: int
    break_extreme: Decimal
    has_displacement: bool
    has_external_sweep: bool
    has_failure_swing: bool


class StructureShiftReplayService:
    """Replay CHoCH -> MSS after liquidity evidence exists."""

    def __init__(
        self,
        candles: CandleRepository,
        events: EngineEventRepository,
        evidence: IctEvidenceRepository,
        clock: Clock,
        state: EngineStateManager,
        *,
        algo_version: str = STRUCTURE_SHIFT_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._events = events
        self._evidence = evidence
        self._clock = clock
        self._state = state
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> StructureShiftReplayReport:
        if end <= start:
            raise ValueError("end must be greater than start")

        candles = list(
            await self._candles.fetch_series(
                symbol,
                timeframe,
                start,
                end,
            )
        )

        if not candles:
            return StructureShiftReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                choch_created=0,
                mss_created=0,
                failed_candidates=0,
                events_inserted=0,
                trend_state=TrendState.RANGING.value,
            )

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        liquidity = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            start,
            end,
        )

        machine = TrendStateMachine()

        consumed_choch: set[
            tuple[
                int,
                SwingStrength,
            ]
        ] = set()

        candidate: _MssCandidate | None = None

        choch_created = 0
        mss_created = 0
        failed_candidates = 0
        inserted = 0

        external_window = swing_window(SwingStrength.EXTERNAL)
        internal_window = swing_window(SwingStrength.INTERNAL)

        for candle_index, candle in enumerate(candles):
            confirmed_external = tuple(
                swing for swing in external_swings if swing.index + external_window <= candle_index
            )

            confirmed_internal = tuple(
                swing for swing in internal_swings if swing.index + internal_window <= candle_index
            )

            external_classified = classify_swings(confirmed_external)
            internal_classified = classify_swings(confirmed_internal)

            # §3.4's entry edge, from the machine that owns it. This module
            # had its own copy of the rule and `structure_replay` had a third,
            # returning a different type -- three implementations of one line
            # of doctrine, and the one the BOS gate consulted was the one that
            # did not persist.
            machine.apply_structure(external_classified)

            if candidate is not None:
                candidate.has_displacement = (
                    candidate.has_displacement
                    or _has_directional_displacement(
                        candles,
                        candidate.direction,
                        (
                            candidate.choch_index - 1,
                            candidate.choch_index,
                            candidate.choch_index + 1,
                        ),
                        max_index=candle_index,
                    )
                )

                elapsed = candle_index - candidate.choch_index

                if 1 <= elapsed <= _MSS_FOLLOWTHROUGH_MAX:
                    has_followthrough = _has_followthrough(
                        candle,
                        candidate.direction,
                        candidate.break_extreme,
                    )

                    if has_followthrough:
                        decision = evaluate_mss(
                            MssEvidence(
                                direction=candidate.direction,
                                has_choch=True,
                                has_displacement=(candidate.has_displacement),
                                has_external_sweep=(candidate.has_external_sweep),
                                has_failure_swing=(candidate.has_failure_swing),
                                followthrough_candles=elapsed,
                            )
                        )

                        if decision.confirmed:
                            if await self._persist_mss(
                                symbol=symbol,
                                timeframe=timeframe,
                                candle=candle,
                                candle_index=candle_index,
                                candidate=candidate,
                                followthrough_candles=elapsed,
                            ):
                                inserted += 1
                                mss_created += 1

                            machine.apply_mss(
                                candidate.direction,
                            )

                            candidate = None

                if candidate is not None and elapsed >= _MSS_FOLLOWTHROUGH_MAX:
                    machine.fail_mss_candidate()
                    candidate = None
                    failed_candidates += 1

            if candidate is not None:
                continue

            if machine.state not in {
                TrendState.BULLISH,
                TrendState.BEARISH,
            }:
                continue

            choch = _find_choch(
                candle=candle,
                trend=machine.state,
                external_classified=external_classified,
                internal_classified=internal_classified,
                consumed=consumed_choch,
            )

            if choch is None:
                continue

            swing, direction = choch

            consumed_choch.add(
                (
                    swing.index,
                    swing.strength,
                )
            )

            if await self._persist_choch(
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
                candle_index=candle_index,
                swing=swing,
                direction=direction,
                previous_trend=machine.state,
            ):
                inserted += 1
                choch_created += 1

            machine.apply_choch(direction)

            has_external_sweep = _has_external_sweep(
                liquidity=liquidity,
                direction=direction,
                choch_index=candle_index,
            )

            has_failure_swing = _has_failure_swing(
                external_classified,
                direction=direction,
                choch_index=candle_index,
            )

            has_displacement = _has_directional_displacement(
                candles,
                direction,
                (
                    candle_index - 1,
                    candle_index,
                ),
                max_index=candle_index,
            )

            break_extreme = candle.low if direction is BreakDirection.DOWN else candle.high

            candidate = _MssCandidate(
                direction=direction,
                choch_index=candle_index,
                swing_index=swing.index,
                break_extreme=break_extreme,
                has_displacement=has_displacement,
                has_external_sweep=has_external_sweep,
                has_failure_swing=has_failure_swing,
            )

        # §3.7's state is what §8.2's G2 and §8.3's F6 both ask for, and F6
        # asks for it on the *timeframe above*. A value that exists only in a
        # return object cannot be read across contexts, so the ladder had no
        # way to see it and confluence scored a constant instead.
        await self._save_state(symbol, timeframe, machine.state, candles[-1].open_time)

        return StructureShiftReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            choch_created=choch_created,
            mss_created=mss_created,
            failed_candidates=failed_candidates,
            events_inserted=inserted,
            trend_state=machine.state.value,
        )

    async def _save_state(
        self,
        symbol: str,
        timeframe: Timeframe,
        trend: TrendState,
        last_open_time: datetime,
    ) -> None:
        await self._state.save(
            StructureEngineState(
                symbol=symbol,
                timeframe=timeframe.value,
                algo_version=self._algo_version,
                last_processed_open_time=last_open_time.isoformat(),
                trend_state=trend.value,
            )
        )

    async def _persist_choch(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        swing: SwingPoint,
        direction: BreakDirection,
        previous_trend: TrendState,
    ) -> bool:
        event_type = f"CHOCH_{direction.value}"

        payload = json.dumps(
            {
                "direction": direction.value,
                "break_index": candle_index,
                "candle_close": str(candle.close),
                "swing_index": swing.index,
                "swing_price": str(swing.price),
                "swing_kind": swing.kind.value,
                "swing_strength": swing.strength.value,
                "previous_trend": previous_trend.value,
                "next_trend": (
                    TrendState.BULLISH_CAUTION.value
                    if previous_trend is TrendState.BULLISH
                    else TrendState.BEARISH_CAUTION.value
                ),
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

    async def _persist_mss(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle: Candle,
        candle_index: int,
        candidate: _MssCandidate,
        followthrough_candles: int,
    ) -> bool:
        event_type = f"MSS_{candidate.direction.value}"

        payload = json.dumps(
            {
                "direction": candidate.direction.value,
                "choch_index": candidate.choch_index,
                "followthrough_index": candle_index,
                "followthrough_candles": followthrough_candles,
                "swing_index": candidate.swing_index,
                "has_displacement": candidate.has_displacement,
                "has_external_sweep": candidate.has_external_sweep,
                "has_failure_swing": candidate.has_failure_swing,
                "candle_close": str(candle.close),
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


def _find_choch(
    *,
    candle: Candle,
    trend: TrendState,
    external_classified: tuple[ClassifiedSwing, ...],
    internal_classified: tuple[ClassifiedSwing, ...],
    consumed: set[tuple[int, SwingStrength]],
) -> (
    tuple[
        SwingPoint,
        BreakDirection,
    ]
    | None
):
    if trend is TrendState.BULLISH:
        target_label = StructureLabel.HL
        target_kind = SwingKind.LOW
        direction = BreakDirection.DOWN

    elif trend is TrendState.BEARISH:
        target_label = StructureLabel.LH
        target_kind = SwingKind.HIGH
        direction = BreakDirection.UP

    else:
        return None

    external = [
        item.swing
        for item in external_classified
        if (
            item.label is target_label
            and item.swing.kind is target_kind
            and (
                item.swing.index,
                item.swing.strength,
            )
            not in consumed
        )
    ]

    internal = [
        item.swing
        for item in internal_classified
        if (
            item.label is target_label
            and item.swing.kind is target_kind
            and (
                item.swing.index,
                item.swing.strength,
            )
            not in consumed
        )
    ]

    candidates = external or internal

    if not candidates:
        return None

    swing = max(
        candidates,
        key=lambda item: item.index,
    )

    if direction is BreakDirection.DOWN:
        if candle.close >= swing.price:
            return None
    else:
        if candle.close <= swing.price:
            return None

    return swing, direction


def _has_failure_swing(
    classified: tuple[ClassifiedSwing, ...],
    *,
    direction: BreakDirection,
    choch_index: int,
) -> bool:
    eligible = [item for item in classified if item.swing.index < choch_index]

    if direction is BreakDirection.DOWN:
        highs = [item for item in eligible if item.swing.kind is SwingKind.HIGH]

        return bool(
            highs
            and max(
                highs,
                key=lambda item: item.swing.index,
            ).label
            is StructureLabel.LH
        )

    lows = [item for item in eligible if item.swing.kind is SwingKind.LOW]

    return bool(
        lows
        and max(
            lows,
            key=lambda item: item.swing.index,
        ).label
        is StructureLabel.HL
    )


def _has_external_sweep(
    *,
    liquidity: tuple[LiquidityEvidenceRecord, ...],
    direction: BreakDirection,
    choch_index: int,
) -> bool:
    expected_side = "BSL" if direction is BreakDirection.DOWN else "SSL"

    lower_bound = max(
        0,
        choch_index - _MSS_SWEEP_LOOKBACK,
    )

    for record in liquidity:
        if record.reason != "liquidity_sweep":
            continue

        if record.to_state != "SWEPT":
            continue

        if not (lower_bound <= record.candle_index <= choch_index):
            continue

        # This blob is written by our own liquidity service, so a parse failure
        # is not a routine condition -- it is corruption, writer drift, or a
        # schema change nobody migrated. Swallowing it would silently answer
        # "no external sweep", which downgrades an MSS to a CHoCH and leaves no
        # trace of why. Constitution §8.5: silent failure is a violation.
        try:
            evidence = json.loads(record.evidence)
        except json.JSONDecodeError as exc:
            raise DomainInvariantError(
                "liquidity evidence is not valid JSON",
                details={
                    "pool_id": record.pool_id,
                    "candle_index": record.candle_index,
                },
            ) from exc

        if evidence.get("liquidity_class") != "EXTERNAL":
            continue

        if evidence.get("side") != expected_side:
            continue

        return True

    return False


def _has_directional_displacement(
    candles: list[Candle],
    direction: BreakDirection,
    indices: tuple[int, ...],
    *,
    max_index: int,
) -> bool:
    expected = (
        DisplacementDirection.BEARISH
        if direction is BreakDirection.DOWN
        else DisplacementDirection.BULLISH
    )

    for index in indices:
        if index < 0:
            continue

        if index > max_index:
            continue

        if index >= len(candles):
            continue

        atr = _atr_at(
            candles,
            index,
        )

        displacement = detect_displacement(
            candles,
            index,
            atr=atr,
        )

        if displacement is not None and displacement.direction is expected:
            return True

    return False


def _has_followthrough(
    candle: Candle,
    direction: BreakDirection,
    break_extreme: Decimal,
) -> bool:
    if direction is BreakDirection.DOWN:
        return candle.close < break_extreme

    return candle.close > break_extreme


def _atr_at(
    candles: Sequence[Candle],
    index: int,
) -> Decimal:
    """Wilder ATR (SLS §2), with the seeding window reported as zero.

    The domain function returns None while ATR is still seeding. Every call
    site in this module already guards with ``if atr <= 0``, so zero routes to
    the same skip; this shim avoids threading Optional through them all.
    §1.9's warm-up gate keeps production out of the seeding region.
    """

    return wilder_atr(candles, index) or Decimal("0")
