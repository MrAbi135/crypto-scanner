"""Liquidity history replay service (Sprint S5)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.application.ports import (
    CandleRepository,
    Clock,
)
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityPoolRepository,
    LiquidityStateStore,
    LiquidityTransitionRecord,
    LiquidityTransitionRepository,
)
from scanner.domain.common import Candle, detection_is_warm
from scanner.domain.liquidity import (
    LiquidityClass,
    LiquidityPool,
    LiquiditySide,
    PoolSource,
    PoolState,
    PoolStrength,
    SweepEvent,
    detect_single_candle_sweep,
    detect_two_candle_sweep,
    pool_from_swing,
    should_expire_pool,
)
from scanner.domain.structure import (
    SwingPoint,
    detect_external_swings,
    detect_internal_swings,
    swing_window,
)
from scanner.shared import Timeframe

LIQUIDITY_ALGO_VERSION = "s5-v1"

_ATR_PERIOD = 14
_EPSILON_ATR = Decimal("0.05")
_SWEEP_SCAN_ATR = Decimal("3")


@dataclass(frozen=True, slots=True)
class LiquidityReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    internal_pools: int
    external_pools: int
    pools_upserted: int
    active_pools: int
    sweeps: int
    broken_pools: int
    expired_pools: int
    last_processed_open_time: datetime | None
    warmup_satisfied: bool = True
    """False when SLS §1.9's closed-candle floor was not met."""


class LiquidityReplayService:
    """Build pools and replay their deterministic lifecycle."""

    def __init__(
        self,
        candles: CandleRepository,
        pools: LiquidityPoolRepository,
        transitions: LiquidityTransitionRepository,
        events: EngineEventRepository,
        snapshots: LiquidityStateStore,
        clock: Clock,
        *,
        algo_version: str = LIQUIDITY_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._pools = pools
        self._transitions = transitions
        self._events = events
        self._snapshots = snapshots
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> LiquidityReplayReport:
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

        if not detection_is_warm(len(candles)):
            await self._snapshots.save(
                symbol,
                timeframe,
                (),
            )

            return LiquidityReplayReport(
                symbol=symbol,
                timeframe=timeframe,
                warmup_satisfied=False,
                candles=len(candles),
                internal_pools=0,
                external_pools=0,
                pools_upserted=0,
                active_pools=0,
                sweeps=0,
                broken_pools=0,
                expired_pools=0,
                last_processed_open_time=(candles[-1].open_time if candles else None),
            )

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        internal_count = 0
        external_count = 0
        upserted = 0

        for swing in internal_swings:
            persisted = await self._persist_swing_pool(
                symbol,
                timeframe,
                swing,
                candles,
                liquidity_class=LiquidityClass.INTERNAL,
            )

            if persisted:
                internal_count += 1
                upserted += 1

        for swing in external_swings:
            persisted = await self._persist_swing_pool(
                symbol,
                timeframe,
                swing,
                candles,
                liquidity_class=LiquidityClass.EXTERNAL,
            )

            if persisted:
                external_count += 1
                upserted += 1

        lifecycle_pools = await self._pools.list_active(
            symbol,
            timeframe,
        )

        sweeps = 0
        broken = 0
        expired = 0

        for pool in lifecycle_pools:
            result = await self._replay_pool_lifecycle(
                pool,
                candles,
            )

            if result == "SWEPT":
                sweeps += 1
            elif result == "BROKEN":
                broken += 1
            elif result == "EXPIRED":
                expired += 1

        active = await self._pools.list_active(
            symbol,
            timeframe,
        )

        await self._snapshots.save(
            symbol,
            timeframe,
            active,
        )

        return LiquidityReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=len(candles),
            internal_pools=internal_count,
            external_pools=external_count,
            pools_upserted=upserted,
            active_pools=len(active),
            sweeps=sweeps,
            broken_pools=broken,
            expired_pools=expired,
            last_processed_open_time=candles[-1].open_time,
        )

    async def _persist_swing_pool(
        self,
        symbol: str,
        timeframe: Timeframe,
        swing: SwingPoint,
        candles: Sequence[Candle],
        *,
        liquidity_class: LiquidityClass,
    ) -> bool:
        confirmation_index = swing.index + swing_window(swing.strength)

        if confirmation_index >= len(candles):
            return False

        confirmation_candle = candles[confirmation_index]

        age_candles = max(
            0,
            len(candles) - 1 - confirmation_index,
        )

        timeframe_rank, max_rank = _timeframe_rank(timeframe)

        pool_id = _build_pool_id(
            symbol=symbol,
            timeframe=timeframe,
            swing=swing,
            algo_version=self._algo_version,
        )

        pool = pool_from_swing(
            swing,
            pool_id=pool_id,
            liquidity_class=liquidity_class,
            touches=0,
            timeframe_rank=timeframe_rank,
            max_timeframe_rank=max_rank,
            age_candles=age_candles,
        )

        evidence = json.dumps(
            {
                "algo_version": self._algo_version,
                "source": "confirmed_swing",
                "swing_index": swing.index,
                "confirmation_index": confirmation_index,
                "swing_open_time": swing.open_time.isoformat(),
                "confirmation_close_time": (confirmation_candle.close_time.isoformat()),
                "swing_strength": swing.strength.value,
                "swing_kind": swing.kind.value,
                "source_price": str(swing.price),
                "touches": 0,
                "age_candles": age_candles,
                "strength_components": {
                    "touches": str(pool.strength.touches_component),
                    "timeframe": str(pool.strength.timeframe_component),
                    "age": str(pool.strength.age_component),
                    "cluster": str(pool.strength.cluster_component),
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        record = LiquidityPoolRecord(
            pool_id=pool.pool_id,
            symbol=symbol,
            timeframe=timeframe,
            side=pool.side.value,
            liquidity_class=pool.liquidity_class.value,
            source=pool.source.value,
            price=pool.price,
            band_low=pool.band_low,
            band_high=pool.band_high,
            strength=pool.strength.total,
            state=pool.state.value,
            member_count=pool.member_count,
            created_index=confirmation_index,
            created_at=confirmation_candle.close_time,
            updated_at=self._clock.now(),
            evidence=evidence,
        )

        await self._pools.upsert(record)

        return True

    async def _replay_pool_lifecycle(
        self,
        record: LiquidityPoolRecord,
        candles: Sequence[Candle],
    ) -> str | None:
        if record.state != "ACTIVE":
            return None

        pool = _to_domain_pool(record)

        start_index = record.created_index + 1

        if start_index >= len(candles):
            return None

        index = start_index

        while index < len(candles):
            candle = candles[index]

            age_candles = index - record.created_index

            if should_expire_pool(age_candles=age_candles):
                transitioned = await self._transition_pool(
                    record,
                    to_state="EXPIRED",
                    reason="pool_max_age",
                    candle_index=index,
                    transitioned_at=candle.close_time,
                    evidence={
                        "age_candles": age_candles,
                    },
                )

                if transitioned:
                    return "EXPIRED"

                return None

            atr = _atr_at(
                candles,
                index,
            )

            if atr <= 0:
                index += 1
                continue

            if not _within_sweep_scan_range(
                pool,
                candle,
                atr,
            ):
                index += 1
                continue

            epsilon = _EPSILON_ATR * atr

            sweep = detect_single_candle_sweep(
                candle,
                pool,
                candle_index=index,
                atr=atr,
                epsilon=epsilon,
            )

            if sweep is not None:
                transitioned = await self._record_sweep(
                    record,
                    sweep,
                )

                if transitioned:
                    return "SWEPT"

                return None

            if _is_close_through(
                pool,
                candle,
                epsilon,
            ):
                transitioned = await self._transition_pool(
                    record,
                    to_state="BROKEN",
                    reason="close_through",
                    candle_index=index,
                    transitioned_at=candle.close_time,
                    evidence={
                        "close": str(candle.close),
                        "level": str(pool.sweep_level),
                        "epsilon": str(epsilon),
                    },
                )

                if transitioned:
                    return "BROKEN"

                return None

            if _is_marginal_penetration(
                pool,
                candle,
                epsilon,
            ):
                next_index = index + 1

                if next_index >= len(candles):
                    return None

                confirmation = candles[next_index]

                two_candle_sweep = detect_two_candle_sweep(
                    candle,
                    confirmation,
                    pool,
                    confirmation_index=next_index,
                    atr=atr,
                    epsilon=epsilon,
                )

                if two_candle_sweep is not None:
                    transitioned = await self._record_sweep(
                        record,
                        two_candle_sweep,
                    )

                    if transitioned:
                        return "SWEPT"

                    return None

                transitioned = await self._transition_pool(
                    record,
                    to_state="BROKEN",
                    reason="two_candle_rejection_failed",
                    candle_index=next_index,
                    transitioned_at=confirmation.close_time,
                    evidence={
                        "penetration_close": str(candle.close),
                        "next_close": str(confirmation.close),
                        "level": str(pool.sweep_level),
                    },
                )

                if transitioned:
                    return "BROKEN"

                return None

            index += 1

        return None

    async def _record_sweep(
        self,
        record: LiquidityPoolRecord,
        sweep: SweepEvent,
    ) -> bool:
        evidence = {
            "pool_id": sweep.pool_id,
            "side": sweep.side.value,
            "liquidity_class": sweep.liquidity_class.value,
            "reference_level": str(sweep.reference_level),
            "penetration_price": str(sweep.penetration_price),
            "close_back_price": str(sweep.close_back_price),
            "sweep_depth_atr": str(sweep.sweep_depth_atr),
            "confirmation_window": (sweep.confirmation_window),
            "gap_sweep": sweep.gap_sweep,
            "reclaimed": sweep.reclaimed,
            "displaced_after": sweep.displaced_after,
            "setup_expiry_index": (sweep.setup_expiry_index),
        }

        transitioned = await self._transition_pool(
            record,
            to_state="SWEPT",
            reason="liquidity_sweep",
            candle_index=sweep.confirmed_index,
            transitioned_at=sweep.confirmed_at,
            evidence=evidence,
        )

        if not transitioned:
            return False

        payload = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
        )

        await self._events.append(
            EngineEventRecord(
                event_key=_build_liquidity_event_key(
                    symbol=record.symbol,
                    timeframe=record.timeframe,
                    event_type="LIQUIDITY_SWEEP",
                    event_at=sweep.confirmed_at,
                    algo_version=self._algo_version,
                    object_id=record.pool_id,
                ),
                symbol=record.symbol,
                timeframe=record.timeframe,
                event_type="LIQUIDITY_SWEEP",
                event_at=sweep.confirmed_at,
                algo_version=self._algo_version,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

        return True

    async def _transition_pool(
        self,
        record: LiquidityPoolRecord,
        *,
        to_state: str,
        reason: str,
        candle_index: int,
        transitioned_at: datetime,
        evidence: Mapping[str, object],
    ) -> bool:
        changed = await self._pools.transition(
            record.pool_id,
            to_state=to_state,
            updated_at=self._clock.now(),
        )

        if not changed:
            return False

        evidence_json = json.dumps(
            dict(evidence),
            sort_keys=True,
            separators=(",", ":"),
        )

        transition_id = _build_transition_id(
            pool_id=record.pool_id,
            to_state=to_state,
            candle_index=candle_index,
            transitioned_at=transitioned_at,
        )

        await self._transitions.append(
            LiquidityTransitionRecord(
                transition_id=transition_id,
                pool_id=record.pool_id,
                symbol=record.symbol,
                timeframe=record.timeframe,
                from_state="ACTIVE",
                to_state=to_state,
                reason=reason,
                transitioned_at=transitioned_at,
                candle_index=candle_index,
                evidence=evidence_json,
            )
        )

        return True


def _to_domain_pool(
    record: LiquidityPoolRecord,
) -> LiquidityPool:
    return LiquidityPool(
        pool_id=record.pool_id,
        side=LiquiditySide(record.side),
        liquidity_class=LiquidityClass(record.liquidity_class),
        source=PoolSource(record.source),
        price=record.price,
        band_low=record.band_low,
        band_high=record.band_high,
        created_at=record.created_at,
        created_index=record.created_index,
        strength=PoolStrength(
            touches_component=record.strength,
            timeframe_component=Decimal("0"),
            age_component=Decimal("0"),
            cluster_component=Decimal("0"),
        ),
        state=PoolState(record.state),
        member_count=record.member_count,
    )


def _is_close_through(
    pool: LiquidityPool,
    candle: Candle,
    epsilon: Decimal,
) -> bool:
    level = pool.sweep_level

    if pool.side is LiquiditySide.BSL:
        return candle.close > level + epsilon

    return candle.close < level - epsilon


def _is_marginal_penetration(
    pool: LiquidityPool,
    candle: Candle,
    epsilon: Decimal,
) -> bool:
    level = pool.sweep_level

    if pool.side is LiquiditySide.BSL:
        return candle.high > level + epsilon and level < candle.close <= level + epsilon

    return candle.low < level - epsilon and level - epsilon <= candle.close < level


def _within_sweep_scan_range(
    pool: LiquidityPool,
    candle: Candle,
    atr: Decimal,
) -> bool:
    limit = _SWEEP_SCAN_ATR * atr
    level = pool.sweep_level

    if pool.side is LiquiditySide.BSL:
        return abs(level - candle.high) <= limit

    return abs(candle.low - level) <= limit


def _atr_at(
    candles: Sequence[Candle],
    index: int,
) -> Decimal:
    start = max(
        0,
        index - _ATR_PERIOD + 1,
    )

    true_ranges: list[Decimal] = []

    for current_index in range(
        start,
        index + 1,
    ):
        candle = candles[current_index]

        if current_index == 0:
            true_range = candle.high - candle.low
        else:
            previous_close = candles[current_index - 1].close

            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )

        true_ranges.append(true_range)

    if not true_ranges:
        return Decimal("0")

    return sum(
        true_ranges,
        Decimal("0"),
    ) / Decimal(len(true_ranges))


def _build_pool_id(
    *,
    symbol: str,
    timeframe: Timeframe,
    swing: SwingPoint,
    algo_version: str,
) -> str:
    raw = "|".join(
        (
            algo_version,
            symbol,
            timeframe.value,
            swing.strength.value,
            swing.kind.value,
            str(swing.index),
            str(swing.price),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_liquidity_event_key(
    *,
    symbol: str,
    timeframe: Timeframe,
    event_type: str,
    event_at: datetime,
    algo_version: str,
    object_id: str,
) -> str:
    raw = "|".join(
        (
            symbol,
            timeframe.value,
            event_type,
            event_at.isoformat(),
            algo_version,
            object_id,
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_transition_id(
    *,
    pool_id: str,
    to_state: str,
    candle_index: int,
    transitioned_at: datetime,
) -> str:
    raw = "|".join(
        (
            pool_id,
            to_state,
            str(candle_index),
            transitioned_at.isoformat(),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _timeframe_rank(
    timeframe: Timeframe,
) -> tuple[int, int]:
    ordered = sorted(
        Timeframe,
        key=lambda item: item.duration,
    )

    rank = ordered.index(timeframe) + 1

    return rank, len(ordered)
