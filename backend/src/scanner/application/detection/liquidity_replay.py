"""Liquidity history replay service (Sprint S5)."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from scanner.application.ports import (
    CandleRepository,
    Clock,
)
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.application.ports.ict_evidence import (
    IctEvidenceRepository,
    LiquidityEvidenceRecord,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityPoolRepository,
    LiquidityStateStore,
    LiquidityTransitionRecord,
    LiquidityTransitionRepository,
)
from scanner.domain.common import (
    TOLERANCE_ATR,
    Candle,
    detection_is_warm,
    quantise_derived,
    wilder_atr_series,
)
from scanner.domain.ict import DisplacementDirection, detect_displacement
from scanner.domain.liquidity import (
    EqualLevelCluster,
    LiquidityClass,
    LiquidityPool,
    LiquiditySide,
    PoolSource,
    PoolState,
    PoolStrength,
    StopHuntEvent,
    SweepEvent,
    count_pool_touches,
    detect_equal_level_clusters,
    detect_single_candle_sweep,
    detect_stop_hunt,
    detect_two_candle_sweep,
    mark_displaced_after,
    mark_stop_hunt_failed,
    pool_from_cluster,
    pool_from_swing,
    should_expire_pool,
    sweep_reclaimed,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    detect_external_swings,
    detect_internal_swings,
    swing_window,
)
from scanner.shared import Timeframe

# v8: two detector-output changes from the 2026-08-29 full-domain review.
# (1) §4.3 cluster chains advance by a consumed-set instead of a member count,
# so a gap-skipped member can seed its own chain and a consumed member can no
# longer seed an overlapping duplicate. (2) §4.2 touches are counted with each
# candle's own ATR-derived epsilon instead of the window-newest candle's, so a
# stored candle's touch verdict no longer moves as the window slides.
#
# v9: sweeps mature. §4.6's `reclaimed` / `displaced_after` and §4.7's stop
# hunt all concern candles that close AFTER the sweep confirms, and a live
# sweep confirms on the newest candle -- so at recording time none of those
# candles existed and the facts were structurally unreachable outside
# backfill. Every pass now revisits recent sweeps and publishes what matured
# as LIQUIDITY_SWEEP_RECLAIMED / LIQUIDITY_SWEEP_DISPLACED /
# LIQUIDITY_STOP_HUNT / LIQUIDITY_STOP_HUNT_FAILED events.
LIQUIDITY_ALGO_VERSION = "s5-v9"

_ATR_PERIOD = 14
_SWEEP_SCAN_ATR = Decimal("3")
# §4.7's stophunt_window is enforced inside `detect_stop_hunt` and
# `mark_displaced_after`; no application-layer copy of the bound exists.


@dataclass(frozen=True, slots=True)
class LiquidityReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    internal_pools: int
    external_pools: int
    clusters: int
    clustered_swings: int
    pools_upserted: int
    active_pools: int
    sweeps: int
    broken_pools: int
    expired_pools: int
    reclassified_pools: int
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
        evidence: IctEvidenceRepository,
        clock: Clock,
        *,
        algo_version: str = LIQUIDITY_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._pools = pools
        self._transitions = transitions
        self._events = events
        self._snapshots = snapshots
        self._evidence = evidence
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
                clusters=0,
                clustered_swings=0,
                pools_upserted=0,
                active_pools=0,
                sweeps=0,
                broken_pools=0,
                expired_pools=0,
                reclassified_pools=0,
                last_processed_open_time=(candles[-1].open_time if candles else None),
            )

        internal_swings = detect_internal_swings(candles)
        external_swings = detect_external_swings(candles)

        atrs = wilder_atr_series(candles)

        clusters = detect_equal_level_clusters(
            [*internal_swings, *external_swings],
            atrs=atrs,
        )

        # §4.2's edge case is a dedup rule, not an optimisation: "one price
        # zone = one pool per side per TF". Cluster members sit within epsilon
        # of each other by construction, so persisting both the cluster pool
        # and each member's own swing pool would put two pools on one level --
        # and a sweep of that level would then transition both, double-counting
        # in anything that ranks or scores pools.
        clustered = {
            (index, cluster.side) for cluster in clusters for index in cluster.member_indices
        }

        # A k=5 pivot is necessarily also a k=2 pivot, so every external swing
        # comes back out of the internal detector too. Registering both put two
        # pools on one price: measured on real BTCUSDT H1, all 238 external
        # swings shared a (swing_index, side) with an internal pool, so 238 of
        # 758 pools were duplicates. §4.2 forbids it -- "one price zone = one
        # pool per side per TF" -- and §4.1 partitions the two: external swings
        # register external levels, internal swings register internal ones
        # "(lower weight)". A swing that is external is external.
        promoted = {(swing.index, swing.kind) for swing in external_swings}

        # §4.2's dedup tolerance, at the scale of the market now rather than
        # at each pool's confirmation: the question the rule asks is whether
        # two levels are one price zone *in the current map*.
        newest_atr = atrs[-1] if atrs else None
        epsilon = TOLERANCE_ATR * (newest_atr or Decimal(0))

        levels = _LevelMap(await self._pools.list_active(symbol, timeframe))

        extremes = _extremes_of(external_swings)

        # §4.4's two classes, counted by the class each pool was actually
        # given. They used to count which loop the swing arrived in --
        # internal-strength swings here, external-strength swings there --
        # which is the static answer §4.4 replaced: how the pivot was
        # detected, not where the level sits. The stored class was corrected
        # and the report was not, so on a series with three INTERNAL pools out
        # of seven it still announced `internal_pools=0`, and the CLI printed
        # that.
        by_class: dict[LiquidityClass, int] = {
            LiquidityClass.INTERNAL: 0,
            LiquidityClass.EXTERNAL: 0,
        }
        upserted = 0

        for swing in internal_swings:
            if (swing.index, swing.kind) in promoted:
                continue

            if (swing.index, _side_of(swing)) in clustered:
                continue

            liquidity_class = extremes.classify(swing.price, LiquidityClass.INTERNAL)

            persisted = await self._persist_swing_pool(
                symbol,
                timeframe,
                swing,
                candles,
                liquidity_class=liquidity_class,
                levels=levels,
                epsilon=epsilon,
                atrs=atrs,
            )

            if persisted:
                by_class[liquidity_class] += 1
                upserted += 1

        for swing in external_swings:
            if (swing.index, _side_of(swing)) in clustered:
                continue

            liquidity_class = extremes.classify(swing.price, LiquidityClass.EXTERNAL)

            persisted = await self._persist_swing_pool(
                symbol,
                timeframe,
                swing,
                candles,
                liquidity_class=liquidity_class,
                levels=levels,
                epsilon=epsilon,
                atrs=atrs,
            )

            if persisted:
                by_class[liquidity_class] += 1
                upserted += 1

        cluster_count = 0

        for cluster in clusters:
            # Classified at the call site like the swing pools, so a cluster
            # pool lands in the §4.4 counts too. `clusters` stays a breakdown
            # by source, which makes internal + external the whole map rather
            # than most of it.
            liquidity_class = extremes.classify(cluster.extreme, LiquidityClass.EXTERNAL)

            if await self._persist_cluster_pool(
                symbol,
                timeframe,
                cluster,
                candles,
                liquidity_class=liquidity_class,
                levels=levels,
                epsilon=epsilon,
                atrs=atrs,
            ):
                by_class[liquidity_class] += 1
                cluster_count += 1
                upserted += 1

        lifecycle_pools = await self._pools.list_active(
            symbol,
            timeframe,
        )

        sweeps = 0
        broken = 0
        expired = 0

        reclassified = 0

        # §4.4: "classification is recomputed when the dealing range updates
        # (a new confirmed external swing re-brackets the range)". The persist
        # loops above only reach pools whose swing is still inside the window,
        # so without this a pool keeps the label it was born with while the
        # range moves out from under it.
        for pool in lifecycle_pools:
            current = LiquidityClass(pool.liquidity_class)
            positional = extremes.classify(pool.price, current)

            if positional is not current:
                await self._pools.upsert(replace(pool, liquidity_class=positional.value))

                reclassified += 1

        for pool in lifecycle_pools:
            result = await self._replay_pool_lifecycle(
                pool,
                candles,
                atrs,
            )

            if result == "SWEPT":
                sweeps += 1
            elif result == "BROKEN":
                broken += 1
            elif result == "EXPIRED":
                expired += 1

        await self._mature_recent_sweeps(
            symbol,
            timeframe,
            candles,
            atrs,
        )

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
            internal_pools=by_class[LiquidityClass.INTERNAL],
            external_pools=by_class[LiquidityClass.EXTERNAL],
            clusters=cluster_count,
            clustered_swings=len(clustered),
            pools_upserted=upserted,
            active_pools=len(active),
            sweeps=sweeps,
            broken_pools=broken,
            expired_pools=expired,
            reclassified_pools=reclassified,
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
        levels: _LevelMap,
        epsilon: Decimal,
        atrs: Sequence[Decimal | None],
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

        side = _side_of(swing)

        if levels.absorbs(side.value, swing.price, pool_id, epsilon):
            return False

        # §4.2's fourth component, counted rather than assumed.
        #
        # Both call sites passed a literal `0` until now, so `touches_component`
        # was exactly zero on all 1,411 pools on the host and no pool could
        # score above 75 of 100 -- a quarter of a formula whose only job is to
        # rank, contributing nothing to the ranking. Same shape as the
        # `cluster_factor` defect `pool_from_cluster` documents.
        #
        # Counted from the confirmation candle forward, which is also why it
        # belongs here rather than at construction: the pool is re-upserted on
        # every pass (see `_LevelMap`), so the count follows the market instead
        # of freezing at the moment of birth, when it is zero by definition.
        touches = count_pool_touches(
            candles[confirmation_index + 1 :],
            side=side,
            band_low=swing.price,
            band_high=swing.price,
            # Per candle, from each candle's own ATR -- the same ε the sweep
            # lifecycle uses on the same walk. One ε from the newest candle
            # made a historical candle's touch verdict move with the window.
            epsilons=_epsilons_for(atrs, confirmation_index + 1, len(candles)),
        )

        pool = pool_from_swing(
            swing,
            pool_id=pool_id,
            liquidity_class=liquidity_class,
            touches=touches,
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
                "touches": touches,
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

        levels.claim(side.value, swing.price, pool_id)

        return True

    async def _persist_cluster_pool(
        self,
        symbol: str,
        timeframe: Timeframe,
        cluster: EqualLevelCluster,
        candles: Sequence[Candle],
        *,
        liquidity_class: LiquidityClass,
        levels: _LevelMap,
        epsilon: Decimal,
        atrs: Sequence[Decimal | None],
    ) -> bool:
        if cluster.confirmed_index >= len(candles):
            return False

        confirmation_candle = candles[cluster.confirmed_index]

        age_candles = max(0, len(candles) - 1 - cluster.confirmed_index)

        timeframe_rank, max_rank = _timeframe_rank(timeframe)

        pool_id = _build_cluster_pool_id(
            symbol=symbol,
            timeframe=timeframe,
            cluster=cluster,
            candles=candles,
            algo_version=self._algo_version,
        )

        if levels.absorbs(cluster.side.value, cluster.extreme, pool_id, epsilon):
            return False

        # The band, not the extreme. §4.2 keeps a cluster's band "for sweep
        # tolerance" precisely because the stops sit across it, so an approach
        # that turns anywhere inside it was rejected by this pool.
        touches = count_pool_touches(
            candles[cluster.confirmed_index + 1 :],
            side=cluster.side,
            band_low=cluster.band_low,
            band_high=cluster.band_high,
            epsilons=_epsilons_for(atrs, cluster.confirmed_index + 1, len(candles)),
        )

        pool = pool_from_cluster(
            cluster,
            pool_id=pool_id,
            # §4.4 classifies by position, and it does not carve out
            # clusters: "internal liquidity: pools ... inside the dealing
            # range". Engineered stops inside the bracket are still internal
            # liquidity. EXTERNAL stays the fallback for a market with no
            # bracket yet, which is what the old static answer assumed
            # everywhere. Decided by the caller now, so the report can count
            # it.
            liquidity_class=liquidity_class,
            created_at=confirmation_candle.close_time,
            touches=touches,
            timeframe_rank=timeframe_rank,
            max_timeframe_rank=max_rank,
            age_candles=age_candles,
        )

        evidence = json.dumps(
            {
                "algo_version": self._algo_version,
                "source": "equal_level_cluster",
                "cluster_id": cluster.cluster_id,
                "member_indices": list(cluster.member_indices),
                "member_prices": [str(price) for price in cluster.member_prices],
                "member_count": cluster.member_count,
                "confirmed_index": cluster.confirmed_index,
                "confirmation_close_time": (confirmation_candle.close_time.isoformat()),
                "band_low": str(cluster.band_low),
                "band_high": str(cluster.band_high),
                "touches": touches,
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

        await self._pools.upsert(
            LiquidityPoolRecord(
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
                created_index=cluster.confirmed_index,
                created_at=confirmation_candle.close_time,
                updated_at=self._clock.now(),
                evidence=evidence,
            )
        )

        levels.claim(cluster.side.value, cluster.extreme, pool_id)

        return True

    async def _replay_pool_lifecycle(
        self,
        record: LiquidityPoolRecord,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
    ) -> str | None:
        if record.state != "ACTIVE":
            return None

        pool = _to_domain_pool(record)

        position = _creation_position(candles, record)

        # §4.2: "ACTIVE -> EXPIRED (age > P.liquidity.pool_max_age = 500
        # candles)". Aged once, against the newest candle, before the walk
        # rather than inside it.
        #
        # Aging inside the walk could not retire anything. The walk ran from
        # `created_index + 1` to the end of a 500-candle window, so the
        # largest age it could reach was 499, and the predicate asks for more
        # than 500. Every pool the rule existed to retire instead took the
        # `start_index >= len(candles)` exit above and stayed ACTIVE forever:
        # 616 of the VM's 641 ACTIVE pools were past retirement, every M5 and
        # M15 pool among them, and `max_pools = 40` was overshot 6x on
        # BTCUSDT M5.
        age_candles = (
            len(candles) - 1 - position
            if position is not None
            else _elapsed_candles(record, candles[-1])
        )

        if should_expire_pool(age_candles=age_candles):
            transitioned = await self._transition_pool(
                record,
                to_state="EXPIRED",
                reason="pool_max_age",
                candle_index=len(candles) - 1,
                transitioned_at=candles[-1].close_time,
                evidence={
                    "age_candles": age_candles,
                },
            )

            if transitioned:
                return "EXPIRED"

            return None

        # Surviving that, the pool is younger than the window, so its creation
        # candle is in it. `None` is reachable only where the window is
        # shorter than `pool_max_age`, and then the whole window is after it.
        index = 0 if position is None else position + 1

        while index < len(candles):
            candle = candles[index]

            atr = _atr_at(
                atrs,
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

            epsilon = TOLERANCE_ATR * atr

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
                        "epsilon": str(quantise_derived(epsilon)),
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
            "sweep_depth_atr": str(quantise_derived(sweep.sweep_depth_atr)),
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

    async def _mature_recent_sweeps(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
    ) -> None:
        """Re-examine §4.6's maturing facts on every pass.

        `reclaimed`, `displaced_after` and §4.7's stop hunt all concern candles
        that close AFTER the sweep confirms, and a live sweep confirms on the
        newest candle -- at recording time none of those candles existed. The
        transition row is append-only and its `false` meant "unknowable then",
        so what matures is published as its own idempotent event, and
        consumers read the events rather than the frozen evidence fields.

        Displacement lives in §5.10, which is the ICT engine. `domain.liquidity`
        may not import `domain.ict` — the Engine-acyclicity contract puts them
        on one layer — so the composition happens here, in the application
        layer, which is above both.
        """
        duration = timeframe.duration

        rows = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            candles[0].open_time,
            candles[-1].close_time + duration,
        )

        for row in rows:
            if row.to_state != "SWEPT" or row.reason != "liquidity_sweep":
                continue

            sweep = _sweep_from_evidence(row, window_open=candles[0].open_time, duration=duration)

            # No "already at the newest candle" skip: a sweep confirmed on the
            # newest candle gets an empty walk range below, so the guard could
            # not fail and was removed.
            if sweep is None:
                continue

            await self._walk_sweep_maturation(
                symbol,
                timeframe,
                sweep,
                candles,
                atrs,
            )

    async def _walk_sweep_maturation(
        self,
        symbol: str,
        timeframe: Timeframe,
        sweep: SweepEvent,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
    ) -> None:
        reversal = (
            DisplacementDirection.BEARISH
            if sweep.side is LiquiditySide.BSL
            else DisplacementDirection.BULLISH
        )

        hunt: StopHuntEvent | None = None

        # A scan-cost cap, not an enforcement: every per-fact window is
        # decided inside the domain functions, and this bound only stops the
        # walk from touching candles no window can reach. Setup expiry (15)
        # is the longest horizon -- a hunt confirms by +3 and fails by +8.
        # Without the cap each swept row would walk to the window's end on
        # every pass, and the engine has a 104s/pass budget.
        last = min(sweep.setup_expiry_index, len(candles) - 1)

        for index in range(max(sweep.confirmed_index + 1, 0), last + 1):
            candle = candles[index]

            was_reclaimed = sweep.reclaimed
            sweep = sweep_reclaimed(sweep, candle, candle_index=index)

            if sweep.reclaimed and not was_reclaimed:
                await self._append_sweep_fact(
                    "LIQUIDITY_SWEEP_RECLAIMED",
                    symbol,
                    timeframe,
                    object_id=sweep.pool_id,
                    event_at=candle.close_time,
                    payload={
                        "pool_id": sweep.pool_id,
                        "side": sweep.side.value,
                        "liquidity_class": sweep.liquidity_class.value,
                        "reference_level": str(sweep.reference_level),
                        "close": str(candle.close),
                        "sweep_confirmed_at": sweep.confirmed_at.isoformat(),
                        "candles_since_confirmation": index - sweep.confirmed_index,
                    },
                )

            atr = _atr_at(atrs, index)

            if atr > 0:
                displacement = detect_displacement(candles, index, atr=atr)

                if displacement is not None and displacement.direction is reversal:
                    was_displaced = sweep.displaced_after

                    sweep = mark_displaced_after(
                        sweep,
                        candle_index=index,
                        displacement_in_reversal_direction=True,
                    )

                    if sweep.displaced_after and not was_displaced:
                        await self._append_sweep_fact(
                            "LIQUIDITY_SWEEP_DISPLACED",
                            symbol,
                            timeframe,
                            object_id=sweep.pool_id,
                            event_at=candle.close_time,
                            payload={
                                "pool_id": sweep.pool_id,
                                "side": sweep.side.value,
                                "liquidity_class": sweep.liquidity_class.value,
                                "displacement_close": str(candle.close),
                                "sweep_confirmed_at": sweep.confirmed_at.isoformat(),
                                "candles_since_confirmation": (index - sweep.confirmed_index),
                            },
                        )

                    if hunt is None:
                        hunt = await self._record_stop_hunt(
                            symbol,
                            timeframe,
                            sweep,
                            candles,
                            displacement_index=index,
                            displacement_direction=(
                                "DOWN"
                                if displacement.direction is DisplacementDirection.BEARISH
                                else "UP"
                            ),
                        )

            if hunt is not None and not hunt.failed:
                was_failed = hunt.failed

                hunt = mark_stop_hunt_failed(
                    hunt,
                    sweep,
                    candle_index=index,
                    candle_close=candle.close,
                    sweep_extreme=sweep.penetration_price,
                )

                if hunt.failed and not was_failed:
                    await self._append_sweep_fact(
                        "LIQUIDITY_STOP_HUNT_FAILED",
                        symbol,
                        timeframe,
                        object_id=sweep.pool_id,
                        event_at=candle.close_time,
                        payload={
                            "sweep_pool_id": hunt.sweep_pool_id,
                            "displacement_id": hunt.displacement_id,
                            "close": str(candle.close),
                            "sweep_extreme": str(sweep.penetration_price),
                            "candles_since_hunt": index - hunt.confirmed_index,
                        },
                    )

    async def _record_stop_hunt(
        self,
        symbol: str,
        timeframe: Timeframe,
        sweep: SweepEvent,
        candles: Sequence[Candle],
        *,
        displacement_index: int,
        displacement_direction: str,
    ) -> StopHuntEvent | None:
        """Detect and publish the §4.7 stop-hunt composite.

        The measured range is the **penetration** candle's, not the
        confirmation candle's (SLS v1.0.4 §4.7). For a single-candle sweep they
        are the same candle, so one rule covers both windows.
        """
        penetration_index = sweep.confirmed_index - (sweep.confirmation_window - 1)

        if penetration_index < 0:
            return None

        penetration = candles[penetration_index]

        hunt = detect_stop_hunt(
            sweep,
            displacement_id=_build_displacement_id(
                symbol=symbol,
                timeframe=timeframe,
                at=candles[displacement_index].close_time,
            ),
            displacement_at=candles[displacement_index].close_time,
            displacement_index=displacement_index,
            # §4.7 speaks in UP/DOWN while §5.10's enum is BULLISH/BEARISH.
            # The two vocabularies are not interchangeable and nothing
            # enforces the mapping, so it is made explicit at the call site.
            displacement_direction=displacement_direction,
            displacement_close=candles[displacement_index].close,
            sweep_candle_high=penetration.high,
            sweep_candle_low=penetration.low,
        )

        if hunt is None:
            return None

        await self._append_sweep_fact(
            "LIQUIDITY_STOP_HUNT",
            symbol,
            timeframe,
            object_id=sweep.pool_id,
            event_at=hunt.confirmed_at,
            payload={
                "algo_version": self._algo_version,
                "sweep_pool_id": hunt.sweep_pool_id,
                "displacement_id": hunt.displacement_id,
                "elapsed_candles": hunt.elapsed_candles,
                "failed": hunt.failed,
                "penetration_index": penetration_index,
                "penetration_high": str(penetration.high),
                "penetration_low": str(penetration.low),
                "displacement_close": str(candles[displacement_index].close),
                "liquidity_class": sweep.liquidity_class.value,
                "side": sweep.side.value,
            },
        )

        return hunt

    async def _append_sweep_fact(
        self,
        event_type: str,
        symbol: str,
        timeframe: Timeframe,
        *,
        object_id: str,
        event_at: datetime,
        payload: Mapping[str, object],
    ) -> None:
        await self._events.append(
            EngineEventRecord(
                event_key=_build_liquidity_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=event_at,
                    algo_version=self._algo_version,
                    object_id=object_id,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=event_at,
                algo_version=self._algo_version,
                payload=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at=self._clock.now(),
            )
        )

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


def _sweep_from_evidence(
    row: LiquidityEvidenceRecord,
    *,
    window_open: datetime,
    duration: timedelta,
) -> SweepEvent | None:
    """Rebuild the SweepEvent a SWEPT transition recorded, in today's window.

    The confirmed index is derived from `transitioned_at`, never from the
    stored `candle_index` -- that index froze in whichever 500-candle window
    recorded the sweep and points nowhere in this one. `transitioned_at` is
    the confirming candle's close time, so its position is one duration back
    from the naive quotient.
    """
    try:
        fields = json.loads(row.evidence)

        return SweepEvent(
            pool_id=row.pool_id,
            side=LiquiditySide(fields["side"]),
            liquidity_class=LiquidityClass(fields["liquidity_class"]),
            confirmed_at=row.transitioned_at,
            confirmed_index=int((row.transitioned_at - window_open) / duration) - 1,
            penetration_price=Decimal(fields["penetration_price"]),
            reference_level=Decimal(fields["reference_level"]),
            close_back_price=Decimal(fields["close_back_price"]),
            sweep_depth_atr=Decimal(fields["sweep_depth_atr"]),
            confirmation_window=int(fields["confirmation_window"]),
            gap_sweep=bool(fields["gap_sweep"]),
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        # Rows written by retired algo versions are historical facts, not
        # this pass's work; a shape this code no longer writes is skipped
        # rather than allowed to kill the whole maturation pass.
        return None


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
    atrs: Sequence[Decimal | None],
    index: int,
) -> Decimal:
    """Wilder ATR (SLS §2), with the seeding window reported as zero.

    The domain function returns None while ATR is still seeding. Every call
    site in this module already guards with ``if atr <= 0``, so zero routes to
    the same skip; this shim avoids threading Optional through them all.
    §1.9's warm-up gate keeps production out of the seeding region.
    """

    if index < 0 or index >= len(atrs):
        return Decimal("0")

    return atrs[index] or Decimal("0")


def _epsilons_for(
    atrs: Sequence[Decimal | None],
    start: int,
    end: int,
) -> tuple[Decimal, ...]:
    """Per-candle sweep tolerance for candles[start:end].

    A candle whose ATR is unavailable (warm-up head of the series) gets
    epsilon 0 -- the strictest reading, so an unmeasured candle can neither
    manufacture a touch that a measured one would refuse nor forgive a breach.
    """
    return tuple(
        TOLERANCE_ATR * atr if (atr := atrs[index]) is not None and atr > 0 else Decimal(0)
        for index in range(start, end)
    )


def _side_of(swing: SwingPoint) -> LiquiditySide:
    return LiquiditySide.BSL if swing.kind is SwingKind.HIGH else LiquiditySide.SSL


def _build_cluster_pool_id(
    *,
    symbol: str,
    timeframe: Timeframe,
    cluster: EqualLevelCluster,
    candles: Sequence[Candle],
    algo_version: str,
) -> str:
    """As `_build_pool_id`, and for the same reason.

    `cluster.cluster_id` is its member indices joined -- "BSL:195:203:210" --
    so it renames itself every time the window slides. The member candles'
    open times do not.
    """
    raw = "|".join(
        (
            algo_version,
            symbol,
            timeframe.value,
            "CLUSTER",
            cluster.side.value,
            ":".join(candles[i].open_time.isoformat() for i in cluster.member_indices),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Extremes:
    """§4.4's dealing-range extremes, used to classify pools by position.

    "External liquidity: pools at/beyond the current external dealing range
    extremes (§5.7). Internal liquidity: pools ... *inside* the dealing range."
    The class was assigned statically instead -- internal swing -> INTERNAL
    pool, external swing -> EXTERNAL -- which answers a different question:
    how the pivot was detected, not where the level sits.

    Built from the most recent confirmed external swing on each side rather
    than from `dealing_range_at`, which additionally requires price to be
    bracketed by them. That condition belongs to §5.7's premium/discount
    reading; §4.4 only needs the extremes, and on the VM four of six contexts
    had price outside the bracket, where the stricter helper yields nothing and
    every pool would keep its static label.
    """

    high: Decimal | None
    low: Decimal | None

    def classify(self, price: Decimal, fallback: LiquidityClass) -> LiquidityClass:
        """The fallback stands until both extremes exist.

        With one side unconfirmed there is no range to be inside of, and
        guessing would relabel every pool on a market that has not yet shown
        its bracket.
        """
        if self.high is None or self.low is None:
            return fallback

        if price >= self.high or price <= self.low:
            return LiquidityClass.EXTERNAL

        return LiquidityClass.INTERNAL


def _extremes_of(swings: Sequence[SwingPoint]) -> _Extremes:
    """§5.7's anchors: the *most recent* external swing on each side.

    Most recent, not highest and lowest. The range "re-anchors whenever a new
    external swing confirms", so a taller high from earlier in the window is
    not the current bracket -- taking the extreme value would hold the range
    open long after the market re-drew it.

    Each side independently, because a new high does not invalidate the low it
    is measured against. This mirrors `dealing_range_at`, which cannot be
    reused here: it also demands price sit between the two.
    """
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    lows = [s for s in swings if s.kind is SwingKind.LOW]

    return _Extremes(
        high=max(highs, key=lambda s: s.index).price if highs else None,
        low=max(lows, key=lambda s: s.index).price if lows else None,
    )


class _LevelMap:
    """The price zones already claimed on this symbol-TF.

    §4.2's Edge Case is a rule about the map, not about one pool: "overlapping
    pools within epsilon merge into one pool with combined evidence (dedup
    rule: one price zone = one pool per side per TF)". Two swings that are not
    the same swing can still be the same zone, and §4.3's clustering only
    groups the ones it was given in the same pass.

    A level claimed by the pool being rewritten is not a collision: the same
    pool re-detected on a later pass must still upsert, or its strength and
    age stop maturing. So the pool_id is compared, not just the price.
    """

    __slots__ = ("_claimed",)

    def __init__(self, pools: Iterable[LiquidityPoolRecord]) -> None:
        self._claimed = [(pool.side, pool.price, pool.pool_id) for pool in pools]

    def absorbs(self, side: str, price: Decimal, pool_id: str, epsilon: Decimal) -> bool:
        return any(
            claimed_side == side and claimed_id != pool_id and abs(claimed_price - price) <= epsilon
            for claimed_side, claimed_price, claimed_id in self._claimed
        )

    def claim(self, side: str, price: Decimal, pool_id: str) -> None:
        self._claimed.append((side, price, pool_id))


def _creation_position(
    candles: Sequence[Candle],
    record: LiquidityPoolRecord,
) -> int | None:
    """Where the pool's creation candle sits in *this* window, or None.

    The stored `created_index` cannot answer this. It is a position in the
    window of the pass that wrote it, and the window slides one candle per
    close, so by the next pass it names a different candle. `created_at` names
    the same one from any window.
    """
    position = bisect_left(candles, record.created_at, key=lambda c: c.close_time)

    if position < len(candles) and candles[position].close_time == record.created_at:
        return position

    return None


def _elapsed_candles(record: LiquidityPoolRecord, newest: Candle) -> int:
    """Age for a pool whose creation candle is no longer in the window.

    Counted in elapsed time rather than in candles, which over-states the age
    across a DEGRADED gap. That is why it is the fallback: where the creation
    candle is present the candles themselves are counted, and a pool only
    reaches this path once it is older than the entire window.
    """
    step = record.timeframe.duration

    return max(0, int((newest.close_time - record.created_at) // step))


def _build_pool_id(
    *,
    symbol: str,
    timeframe: Timeframe,
    swing: SwingPoint,
    algo_version: str,
) -> str:
    """Identity anchored in time, because the index is window-local.

    Detection replays a 500-candle window that slides one candle per close,
    and `swing.index` is a position inside it. The same swing high therefore
    hashed to a different id on every pass, and each pass wrote a new pool row
    for a level that already had one -- on the VM, eight ACTIVE BSL pools at
    exactly 70022 on BTCUSDT M5, against §4.2's "one price zone = one pool per
    side per TF". `swing.open_time` names the same candle from any window.

    **Strength is deliberately not part of the identity.** A k=5 external pivot
    is necessarily also a k=2 internal one, and the internal detector confirms
    it three candles sooner. Keyed on strength, the early pass wrote an
    INTERNAL pool and the pass that promoted the swing wrote a second,
    EXTERNAL one at the same price -- measured on the VM as 5 EXTERNAL and 3
    INTERNAL ACTIVE pools at exactly 79500 on BTCUSDT M5, and the same split
    at 77251. The `promoted` guard in `run` only suppresses that within a
    single pass.

    One level is one pool, and the class is a property of it rather than of
    which pool it is -- which is also what §4.4 asks for when it says
    "classification is recomputed when the dealing range updates". `upsert`
    already carries `liquidity_class` in its update set, so the promotion now
    lands on the existing row instead of beside it.
    """
    raw = "|".join(
        (
            algo_version,
            symbol,
            timeframe.value,
            swing.kind.value,
            swing.open_time.isoformat(),
            str(swing.price),
        )
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_displacement_id(
    *,
    symbol: str,
    timeframe: Timeframe,
    at: datetime,
) -> str:
    """Stable id for a displacement the liquidity engine observed.

    Displacements are not persisted anywhere (no DISPLACEMENT event type), so
    a stop hunt cannot reference a stored row. This derives a deterministic id
    from the coordinates that identify the candle, which is enough for the
    evidence chain to be followed back by hand until §5.10 gains a record of
    its own.
    """

    raw = "|".join(("displacement", symbol, timeframe.value, at.isoformat()))

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
    transitioned_at: datetime,
) -> str:
    """See `ict_replay._build_transition_id` — the window-local index is gone."""

    raw = "|".join(
        (
            pool_id,
            to_state,
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
