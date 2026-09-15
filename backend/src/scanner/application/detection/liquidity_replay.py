"""Liquidity history replay service (Sprint S5)."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial

from scanner.application.detection.state import (
    EngineStateManager,
    first_undecided_index,
    mark_decided,
)
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
    score_pool_strength,
    should_expire_pool,
    sweep_reclaimed,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    SwingStrength,
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
# v10: swing walk-back fix (Sec 3.1) -- the flat pause in a descent or
# ascent no longer mints a pivot, so which swings exist changes.
# v11: a sweep's §4.4 class is the class its level had on the candle the
# sweep confirmed, not the class the window's newest extremes give it. The
# SWEPT evidence and every fact matured from it (stop hunt, reclaim,
# displaced) carry that class, so a replay over a longer window no longer
# rewrites it -- measured before the fix on 150 generated series, 1,205 of
# 5,711 sweeps (21%) changed class between a prefix run and the full run.
#
# Pinned reads, 2026-09-14 -- deliberately not a bump. Every read of pools, the
# transition ledger and the sweep facts is pinned to the running version, here
# and in every engine that consumes liquidity. Within one version the output is
# byte-identical, which the golden suite proves; across a bump the new engine no
# longer sweeps, absorbs or matures the previous generation's pools -- measured
# after the s5-v11 deploy, it had re-matured 124 stop hunts and 1,596 reclaims of
# s5-v10 sweeps under its own label -- and only retires them by age.
# v12: each maturing fact is published once per sweep. The walk re-reads a sweep
# from its frozen evidence (`displaced_after`/`reclaimed` false) every pass, and
# the fact's key carries the candle, so when a later pass found a different first
# qualifying candle (window-start ATR drift) the same fact about the same sweep
# was written again: on the host 9 s5-v10 pools carry two stop hunts and 10 two
# displacements; replayed LINKUSDT M5 wrote a pool's pair at 08:20 and again at
# 08:25, 479 candles later.
# v13: the pool map is decided by candle order alone (audit M5/M10 and the
# parked cluster finding, owner ruling 2026-09-14). Each level is judged on the
# candle that confirms it -- that candle's epsilon, the pools alive on it --
# instead of against the pools still ACTIVE now at the newest candle's epsilon,
# so the map no longer depends on which passes ran: offline, 1 to 12 pools per
# context existed only in the close-by-close replay, and on the host 29 cluster
# pools were ACTIVE beside their own member's swing pool. A cluster merges into
# the pool holding its first member (SLS 4.2 "combined evidence"), a pool's
# level is recorded in stages so every candle meets it as it stood then, and a
# pivot's pool is born at its first confirmation.
# v14: a pool is born only on a candle no earlier pass has decided (audit M3,
# owner ruling 2026-09-14). Wilder ATR has no value in a window's first ~14
# candles, so epsilon there is zero and a level another pool held while it
# sat deeper in the window became its own pool once its confirmation reached
# them -- and that pass wrote its old sweep or break 477-486 candles late
# (DOGEUSDT M15: five pools, each born at window index 12 with epsilon 0).
# v15: a sweep's maturation decides each candle once too (audit M3). Every pass
# re-walked each recent sweep across its whole maturation window and judged
# displacement against this window's ATR, so a candle an earlier pass had found
# no displacement on could find one after the window start moved: on the host a
# LINKUSDT M5 LIQUIDITY_SWEEP_DISPLACED was written 456 periods after its candle,
# over two hours after its own sweep and reclaim (2026-09-15). On a decided
# candle a displacement now exists only where this sweep's stop hunt was
# published; reclaim and hunt failure read closes alone and replay as before.
LIQUIDITY_ALGO_VERSION = "s5-v15"

_ATR_PERIOD = 14
_SWEEP_SCAN_ATR = Decimal("3")

# The facts a sweep matures into, each published at most once per sweep.
_SWEEP_FACTS = frozenset(
    {
        "LIQUIDITY_SWEEP_RECLAIMED",
        "LIQUIDITY_SWEEP_DISPLACED",
        "LIQUIDITY_STOP_HUNT",
        "LIQUIDITY_STOP_HUNT_FAILED",
    }
)
_TERMINAL_POOL_STATES = frozenset({"SWEPT", "BROKEN", "EXPIRED"})

# A pool confirmed this early in a window may rest on a pivot the window has cut
# off -- an external pivot needs five candles on its left -- so the walk cannot
# rebuild it and reads it back from persistence instead.
_SEED_HORIZON = 2 * swing_window(SwingStrength.EXTERNAL)

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
        state: EngineStateManager | None = None,
    ) -> None:
        self._candles = candles
        self._pools = pools
        self._transitions = transitions
        self._events = events
        self._snapshots = snapshots
        self._evidence = evidence
        self._clock = clock
        self._algo_version = algo_version
        # The last candle a pass decided (audit M3); absent, the whole window.
        self._state = state

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

        # Reported only. A member's own swing pool is what its cluster merges
        # into, so membership no longer suppresses anything.
        clustered = {
            (index, cluster.side) for cluster in clusters for index in cluster.member_indices
        }

        # §4.2's map, decided by candle order alone (audit M5/M10, owner ruling
        # 2026-09-14). The map used to be rebuilt each pass from the pools still
        # ACTIVE now, with the newest candle's epsilon, in loop order -- so the
        # same candles gave a different map depending on which passes had run.
        # The walk judges every level on the candle that confirms it instead.
        seeds, known = await self._seed_levels(symbol, timeframe, candles)

        decided_before = await first_undecided_index(
            self._state, symbol, timeframe, self._algo_version, candles
        )

        walk = _PoolMapWalk(
            candles,
            atrs,
            seeds=seeds,
            pool_id=partial(
                _build_pool_id,
                symbol=symbol,
                timeframe=timeframe,
                algo_version=self._algo_version,
            ),
            # A level confirmed on a candle an earlier pass decided, with no
            # row, never became a pool when that candle was newest; it is not
            # born now (audit M3).
            decided_before=decided_before,
            known=known,
        )

        levels = walk.run(internal_swings, external_swings, clusters)

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

        cluster_count = 0

        for level in levels:
            # A level held from before this window is persistence's record; it
            # is rewritten only when a cluster in this window merged into it.
            if level.seed is not None and not level.merged:
                continue

            liquidity_class = extremes.classify(level.stage.price, level.class_fallback)

            await self._persist_level(
                symbol,
                timeframe,
                level,
                candles,
                atrs,
                liquidity_class=liquidity_class,
            )

            by_class[liquidity_class] += 1
            upserted += 1

            if level.stage.source is PoolSource.CLUSTER:
                cluster_count += 1

        lifecycle_pools = await self._pools.list_active(
            symbol,
            timeframe,
            only_version=self._algo_version,
        )

        # A previous version's pools are still ACTIVE rows after a bump. They
        # are not this engine's to sweep, break or reclassify -- doing so
        # published this version's facts about the old generation's levels --
        # but left alone they would stay ACTIVE forever. So they only age out.
        current_ids = {pool.pool_id for pool in lifecycle_pools}
        superseded = tuple(
            pool
            for pool in await self._pools.list_active(symbol, timeframe)
            if pool.pool_id not in current_ids
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
                external_swings,
            )

            if result == "SWEPT":
                sweeps += 1
            elif result == "BROKEN":
                broken += 1
            elif result == "EXPIRED":
                expired += 1

        for pool in superseded:
            retired = await self._replay_pool_lifecycle(
                pool,
                candles,
                atrs,
                external_swings,
                expire_only=True,
            )

            if retired == "EXPIRED":
                expired += 1

        await self._mature_recent_sweeps(
            symbol,
            timeframe,
            candles,
            atrs,
            decided_before=decided_before,
        )

        active = await self._pools.list_active(
            symbol,
            timeframe,
            only_version=self._algo_version,
        )

        await self._snapshots.save(
            symbol,
            timeframe,
            active,
        )

        await mark_decided(self._state, symbol, timeframe, self._algo_version, candles)

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

    async def _seed_levels(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: Sequence[Candle],
    ) -> tuple[tuple[_Level, ...], frozenset[str]]:
        """This version's pools the window may no longer be able to rebuild,
        and the ids of every pool of this version the window can see a row for.

        A pool confirmed within `_SEED_HORIZON` candles of the window start may
        rest on a pivot the window has cut off; it still holds its level, and
        the row is the record of that. The ones consumed inside the window are
        read back too -- on the candles before their consumption they held
        their level just the same.
        """
        horizon = candles[min(_SEED_HORIZON, len(candles) - 1)].close_time
        end = candles[-1].close_time + timeframe.duration

        active = await self._pools.list_active(symbol, timeframe, only_version=self._algo_version)
        records = {pool.pool_id: pool for pool in active if pool.created_at < horizon}
        ended: dict[str, datetime] = {}

        ledger = await self._evidence.list_liquidity(
            symbol, timeframe, candles[0].open_time, end, only_version=self._algo_version
        )

        for row in ledger:
            if row.to_state not in _TERMINAL_POOL_STATES:
                continue

            ended[row.pool_id] = min(
                ended.get(row.pool_id, row.transitioned_at), row.transitioned_at
            )

            if row.pool_id not in records:
                record = await self._pools.get(row.pool_id)

                if record is not None and record.created_at < horizon:
                    records[row.pool_id] = record

        known = frozenset({pool.pool_id for pool in active} | {row.pool_id for row in ledger})

        seeds = tuple(
            _Level.from_record(record, ended_at=ended.get(record.pool_id))
            for record in sorted(records.values(), key=lambda item: (item.created_at, item.pool_id))
        )

        return seeds, known

    async def _persist_level(
        self,
        symbol: str,
        timeframe: Timeframe,
        level: _Level,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
        *,
        liquidity_class: LiquidityClass,
    ) -> None:
        stage = level.stage
        timeframe_rank, max_rank = _timeframe_rank(timeframe)
        evidence: dict[str, object]

        if level.seed is not None:
            # A level from before this window that a cluster inside it merged
            # into: its own counts stand, the members and the band changed.
            evidence = _evidence_fields(level.seed)
            touches = _int_field(evidence.get("touches"))
            age_candles = _elapsed_candles(level.seed, candles[-1])
            created_index = level.seed.created_index
            created_at = level.seed.created_at
        else:
            born, swing = level.born, level.swing

            if born is None or swing is None:
                raise ValueError("a level the walk built carries its confirming swing")

            # §4.2's fourth component, counted rather than assumed, from the
            # confirmation candle forward with each candle's own epsilon -- the
            # same epsilon the sweep lifecycle uses on the same walk -- and
            # re-counted every pass, so it follows the market.
            touches = count_pool_touches(
                candles[born + 1 :],
                side=level.side,
                band_low=stage.band_low,
                band_high=stage.band_high,
                epsilons=_epsilons_for(atrs, born + 1, len(candles)),
            )
            age_candles = max(0, len(candles) - 1 - born)
            created_index = born
            created_at = candles[born].close_time
            evidence = {
                "source": "confirmed_swing",
                "swing_index": swing.index,
                "confirmation_index": born,
                "swing_open_time": swing.open_time.isoformat(),
                "confirmation_close_time": created_at.isoformat(),
                "swing_strength": level.swing_strength.value,
                "swing_kind": swing.kind.value,
                "source_price": str(swing.price),
            }

        strength = score_pool_strength(
            touches=touches,
            timeframe_rank=timeframe_rank,
            max_timeframe_rank=max_rank,
            age_candles=age_candles,
            member_count=stage.member_count,
        )

        if level.cluster is not None:
            cluster, count = level.cluster
            members = cluster.member_indices[:count]

            evidence.update(
                {
                    "source": "equal_level_cluster",
                    "cluster_id": cluster.cluster_id,
                    "member_indices": list(members),
                    "member_open_times": [candles[i].open_time.isoformat() for i in members],
                    "member_prices": [str(price) for price in cluster.member_prices[:count]],
                    "member_count": stage.member_count,
                    "confirmed_index": cluster.confirmed_index,
                    "band_low": str(stage.band_low),
                    "band_high": str(stage.band_high),
                }
            )

        evidence.update(
            {
                "algo_version": self._algo_version,
                "touches": touches,
                "age_candles": age_candles,
                "stages": [item.to_json() for item in level.stages],
                "strength_components": {
                    "touches": str(strength.touches_component),
                    "timeframe": str(strength.timeframe_component),
                    "age": str(strength.age_component),
                    "cluster": str(strength.cluster_component),
                },
            }
        )

        await self._pools.upsert(
            LiquidityPoolRecord(
                pool_id=level.pool_id,
                symbol=symbol,
                timeframe=timeframe,
                side=level.side.value,
                liquidity_class=liquidity_class.value,
                source=stage.source.value,
                price=stage.price,
                band_low=stage.band_low,
                band_high=stage.band_high,
                strength=strength.total,
                state="ACTIVE",
                member_count=stage.member_count,
                created_index=created_index,
                created_at=created_at,
                updated_at=self._clock.now(),
                evidence=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            )
        )

    async def _replay_pool_lifecycle(
        self,
        record: LiquidityPoolRecord,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
        external_swings: Sequence[SwingPoint],
        *,
        expire_only: bool = False,
    ) -> str | None:
        if record.state != "ACTIVE":
            return None

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

        # Another version's pool: aged, never swept or broken by this one.
        if expire_only:
            return None

        # Surviving that, the pool is younger than the window, so its creation
        # candle is in it. `None` is reachable only where the window is
        # shorter than `pool_max_age`, and then the whole window is after it.
        index = 0 if position is None else position + 1

        # Judged stage by stage: each candle meets the pool as it stood when
        # that candle closed, not as a later merge left it.
        terminal, _ = _walk_to_terminal(
            _stage_pools(record),
            candles,
            atrs,
            index,
            len(candles) - 1,
        )

        if terminal is None:
            return None

        if terminal.sweep is not None:
            sweep = terminal.sweep

            transitioned = await self._record_sweep(
                record,
                _classified_when_swept(sweep, record, external_swings),
            )

            return "SWEPT" if transitioned else None

        transitioned = await self._transition_pool(
            record,
            to_state="BROKEN",
            reason=terminal.reason,
            candle_index=terminal.index,
            transitioned_at=candles[terminal.index].close_time,
            evidence=terminal.evidence,
        )

        return "BROKEN" if transitioned else None

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
        *,
        decided_before: int = 0,
    ) -> None:
        """Re-examine §4.6's maturing facts on every pass.

        `decided_before` is the first candle of this window no earlier pass has
        decided (audit M3); 0 judges the whole window, as a version's first pass
        and the golden harness do.

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

        # Only this version's sweeps. Unpinned, the pass after a bump matured
        # the previous generation's sweeps and published their facts under
        # this version's label, carrying the class the bump had corrected.
        rows = await self._evidence.list_liquidity(
            symbol,
            timeframe,
            candles[0].open_time,
            candles[-1].close_time + duration,
            only_version=self._algo_version,
        )

        # What this version already published, by (fact, sweep). The walk below
        # starts from the frozen evidence every pass, so without this a fact
        # whose first qualifying candle moved between passes was written again.
        published = await self._published_sweep_facts(
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
                published,
                decided_before=decided_before,
            )

    async def _published_sweep_facts(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> dict[tuple[str, str], datetime]:
        """What this version published, by (fact, sweep), with the candle it
        named -- the walk replays a decided candle from it."""
        facts: dict[tuple[str, str], datetime] = {}

        for event in await self._events.list_events(symbol, timeframe, start, end):
            if event.algo_version != self._algo_version or event.event_type not in _SWEEP_FACTS:
                continue

            payload = json.loads(event.payload)
            pool_id = payload.get("pool_id") or payload.get("sweep_pool_id")

            if isinstance(pool_id, str):
                facts.setdefault((event.event_type, pool_id), event.event_at)

        return facts

    async def _walk_sweep_maturation(
        self,
        symbol: str,
        timeframe: Timeframe,
        sweep: SweepEvent,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
        published: dict[tuple[str, str], datetime],
        *,
        decided_before: int,
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
                await self._append_sweep_fact_once(
                    published,
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

            if index < decided_before:
                # Decided on the pass where it was newest, against that window's
                # ATR (audit M3). Judged again here it reads an ATR seeded at a
                # later window start, and a "no" became a DISPLACED written 456
                # periods late. What that pass's "yes" still has to drive is
                # the stop hunt, whose failure later candles track -- so a
                # decided candle displaces exactly where the hunt was published.
                # Its DISPLACED needs no replay: it was published then, or it is
                # not this pass's to publish.
                reversed_here = candle.close_time == published.get(
                    ("LIQUIDITY_STOP_HUNT", sweep.pool_id)
                )
            else:
                atr = _atr_at(atrs, index)
                displacement = detect_displacement(candles, index, atr=atr) if atr > 0 else None
                reversed_here = displacement is not None and displacement.direction is reversal

            if reversed_here:
                was_displaced = sweep.displaced_after

                sweep = mark_displaced_after(
                    sweep,
                    candle_index=index,
                    displacement_in_reversal_direction=True,
                )

                if sweep.displaced_after and not was_displaced:
                    await self._append_sweep_fact_once(
                        published,
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
                        published,
                        displacement_index=index,
                        displacement_direction=(
                            "DOWN" if reversal is DisplacementDirection.BEARISH else "UP"
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
                    await self._append_sweep_fact_once(
                        published,
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
        published: dict[tuple[str, str], datetime],
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

        # Published once per sweep; the hunt is still returned so its failure can
        # be tracked on this pass.
        await self._append_sweep_fact_once(
            published,
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

    async def _append_sweep_fact_once(
        self,
        published: dict[tuple[str, str], datetime],
        event_type: str,
        symbol: str,
        timeframe: Timeframe,
        *,
        object_id: str,
        event_at: datetime,
        payload: Mapping[str, object],
    ) -> None:
        """At most one row of each maturing fact per sweep, whichever candle a
        pass first finds qualifying."""
        if (event_type, object_id) in published:
            return

        published[(event_type, object_id)] = event_at

        await self._append_sweep_fact(
            event_type,
            symbol,
            timeframe,
            object_id=object_id,
            event_at=event_at,
            payload=payload,
        )

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
    stage: _Stage | None = None,
) -> LiquidityPool:
    return LiquidityPool(
        pool_id=record.pool_id,
        side=LiquiditySide(record.side),
        liquidity_class=LiquidityClass(record.liquidity_class),
        source=PoolSource(record.source) if stage is None else stage.source,
        price=record.price if stage is None else stage.price,
        band_low=record.band_low if stage is None else stage.band_low,
        band_high=record.band_high if stage is None else stage.band_high,
        created_at=record.created_at,
        created_index=record.created_index,
        strength=PoolStrength(
            touches_component=record.strength,
            timeframe_component=Decimal("0"),
            age_component=Decimal("0"),
            cluster_component=Decimal("0"),
        ),
        state=PoolState(record.state),
        member_count=record.member_count if stage is None else stage.member_count,
    )


def _stage_pools(record: LiquidityPoolRecord) -> Callable[[Candle], LiquidityPool]:
    """The domain pool each candle meets, one per recorded stage."""
    stages = _stages_of(record)
    pools = [_to_domain_pool(record, stage) for stage in stages]

    def pool_at(candle: Candle) -> LiquidityPool:
        return pools[_stage_index(stages, candle.close_time)]

    return pool_at


@dataclass(frozen=True, slots=True)
class _Terminal:
    """The candle that ends a pool: a sweep, or a break with its evidence."""

    index: int
    sweep: SweepEvent | None
    reason: str
    evidence: Mapping[str, object]


def _walk_to_terminal(
    pool_at: Callable[[Candle], LiquidityPool],
    candles: Sequence[Candle],
    atrs: Sequence[Decimal | None],
    start: int,
    stop: int,
) -> tuple[_Terminal | None, int]:
    """The first candle in [start, stop] that sweeps or breaks the pool (§4.2, §4.6).

    One walk for two callers: the lifecycle, which persists what it finds, and
    the map walk, which asks whether a pool was still alive on a candle. With
    no terminal, the second value is the first candle not yet decided -- a
    marginal penetration on `stop` needs the candle after it.
    """
    index = start

    while index <= stop:
        candle = candles[index]
        atr = _atr_at(atrs, index)

        if atr <= 0:
            index += 1
            continue

        pool = pool_at(candle)

        if not _within_sweep_scan_range(pool, candle, atr):
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
            return _Terminal(index, sweep, "liquidity_sweep", {}), index

        if _is_close_through(pool, candle, epsilon):
            return (
                _Terminal(
                    index,
                    None,
                    "close_through",
                    {
                        "close": str(candle.close),
                        "level": str(pool.sweep_level),
                        "epsilon": str(quantise_derived(epsilon)),
                    },
                ),
                index,
            )

        if _is_marginal_penetration(pool, candle, epsilon):
            next_index = index + 1

            if next_index > stop:
                return None, index

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
                return _Terminal(next_index, two_candle_sweep, "liquidity_sweep", {}), next_index

            return (
                _Terminal(
                    next_index,
                    None,
                    "two_candle_rejection_failed",
                    {
                        "penetration_close": str(candle.close),
                        "next_close": str(confirmation.close),
                        "level": str(pool.sweep_level),
                    },
                ),
                next_index,
            )

        index += 1

    return None, index


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


def _classified_when_swept(
    sweep: SweepEvent,
    record: LiquidityPoolRecord,
    external_swings: Sequence[SwingPoint],
) -> SweepEvent:
    """§4.4's class as it stood on the candle the sweep confirmed.

    `detect_*_sweep` copies the class off the pool row, and the row carries
    the class the *newest* extremes give it -- `run` reclassifies every live
    pool against the whole window before any lifecycle is walked. That is
    right for the row, which is current state. It is wrong for the sweep,
    which is a fact about one candle: the external swings that re-bracketed
    the range afterwards had not happened yet, and neither had the
    confirmation of the ones still inside their own window.

    It was not cosmetic. The class is frozen into the SWEPT evidence and the
    LIQUIDITY_SWEEP payload, maturation reads it back from there, and §4.7's
    stop hunt exists only for an EXTERNAL sweep -- so the same sweep, replayed
    over a longer window, changed class and gained or lost its stop hunt.
    Measured on 150 generated series: 1,205 of 5,711 sweeps a prefix run
    published (21%) changed class in the full run; with this, none do. The
    per-series spread is wide (median 16%, some series over 60%), so a
    small sample can read anywhere from a few percent to a quarter.

    With no bracket yet at that candle, the pool keeps the class it was born
    with rather than the row's current one, for the same reason.
    """

    confirmed = [
        swing
        for swing in external_swings
        if swing.index + swing_window(swing.strength) <= sweep.confirmed_index
    ]

    # The level and its static label as they stood on the penetration candle,
    # so a merge or a promotion after the sweep cannot relabel it.
    penetration_close = sweep.confirmed_at - record.timeframe.duration * (
        sweep.confirmation_window - 1
    )
    fallback = _class_at_birth(record, penetration_close)

    liquidity_class = _extremes_of(confirmed).classify(sweep.reference_level, fallback)

    return replace(sweep, liquidity_class=liquidity_class)


def _class_at_birth(record: LiquidityPoolRecord, at: datetime | None = None) -> LiquidityClass:
    """The static label `run` falls back to, for the pool as it stood at `at`.

    A cluster pool and an external swing's pool are EXTERNAL, an internal
    swing's pool INTERNAL -- the fallbacks the persist step passes to
    `classify`. Read from the stage in force on the candle closing at `at`
    (the newest stage when `at` is None). Anything else keeps its row's class.
    """
    stages = _stages_of(record)
    stage = stages[-1] if at is None else stages[_stage_index(stages, at)]

    if stage.source is PoolSource.CLUSTER:
        return LiquidityClass.EXTERNAL

    if stage.source is PoolSource.SWING and stage.strength is not None:
        return LiquidityClass(stage.strength.value)

    return LiquidityClass(record.liquidity_class)


@dataclass(frozen=True, slots=True)
class _Stage:
    """What a pool was from one candle on (§4.2: "merge into one pool with
    combined evidence").

    A pool's level changes when a cluster merges into it, and its static label
    when its pivot is promoted to external. Each change is a stage that starts
    on the close of the candle that confirmed it and holds from the next
    candle, so every candle is judged against the pool as it stood when that
    candle closed -- never against a merge that came later.
    """

    starts: datetime
    source: PoolSource
    strength: SwingStrength | None
    price: Decimal
    band_low: Decimal
    band_high: Decimal
    member_count: int

    def to_json(self) -> dict[str, object]:
        return {
            "starts": self.starts.isoformat(),
            "source": self.source.value,
            "strength": None if self.strength is None else self.strength.value,
            "price": str(self.price),
            "band_low": str(self.band_low),
            "band_high": str(self.band_high),
            "member_count": self.member_count,
        }

    @staticmethod
    def from_json(fields: Mapping[str, object]) -> _Stage:
        strength = fields.get("strength")

        return _Stage(
            starts=datetime.fromisoformat(str(fields["starts"])),
            source=PoolSource(str(fields["source"])),
            strength=None if strength is None else SwingStrength(str(strength)),
            price=Decimal(str(fields["price"])),
            band_low=Decimal(str(fields["band_low"])),
            band_high=Decimal(str(fields["band_high"])),
            member_count=int(str(fields["member_count"])),
        )


def _evidence_fields(record: LiquidityPoolRecord) -> dict[str, object]:
    try:
        fields = json.loads(record.evidence)
    except (ValueError, TypeError):
        return {}

    return dict(fields) if isinstance(fields, dict) else {}


def _int_field(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0


def _stages_of(record: LiquidityPoolRecord) -> tuple[_Stage, ...]:
    """The stages a row recorded, or the row itself as its only stage."""
    fields = _evidence_fields(record)
    recorded = fields.get("stages")

    if isinstance(recorded, list) and recorded:
        try:
            return tuple(_Stage.from_json(item) for item in recorded)
        except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
            pass

    strength = fields.get("swing_strength")

    return (
        _Stage(
            starts=record.created_at,
            source=PoolSource(record.source),
            strength=(
                SwingStrength(strength)
                if strength in {item.value for item in SwingStrength}
                else None
            ),
            price=record.price,
            band_low=record.band_low,
            band_high=record.band_high,
            member_count=record.member_count,
        ),
    )


def _stage_index(stages: Sequence[_Stage], close_time: datetime) -> int:
    """Which stage the candle closing at `close_time` meets.

    A stage starts on the close of the candle that confirmed its change and
    holds from the next candle on; that candle itself met the stage before.
    """
    return max(bisect_left(stages, close_time, key=lambda stage: stage.starts) - 1, 0)


def _stage_pool(pool_id: str, side: LiquiditySide, stage: _Stage) -> LiquidityPool:
    """A stage as a domain pool, for the map walk's aliveness test.

    The walk asks only on which candle a pool ends, which depends on its level
    and band; the class copied into a sweep there is never read.
    """
    zero = Decimal("0")

    return LiquidityPool(
        pool_id=pool_id,
        side=side,
        liquidity_class=LiquidityClass.EXTERNAL,
        source=stage.source,
        price=stage.price,
        band_low=stage.band_low,
        band_high=stage.band_high,
        created_at=stage.starts,
        created_index=0,
        strength=PoolStrength(
            touches_component=zero,
            timeframe_component=zero,
            age_component=zero,
            cluster_component=zero,
        ),
        member_count=stage.member_count,
    )


def _within(stage: _Stage, low: Decimal, high: Decimal, epsilon: Decimal) -> bool:
    """Does [low, high] lie within epsilon of the stage's band? (§4.2's one zone)"""
    return stage.band_low - epsilon <= high and low <= stage.band_high + epsilon


def _confirmation_of(swing: SwingPoint) -> int:
    return swing.index + swing_window(swing.strength)


@dataclass(slots=True)
class _Level:
    """One pool as `_PoolMapWalk` builds it: read back from before the window
    (a seed) or confirmed inside it (born at a window index, from a swing)."""

    pool_id: str
    side: LiquiditySide
    stages: list[_Stage]
    born: int | None = None
    swing: SwingPoint | None = None
    seed: LiquidityPoolRecord | None = None
    ended_at: datetime | None = None
    ended_index: int | None = None
    judged_from: int = 0
    external: bool = False
    merged: bool = False
    cluster: tuple[EqualLevelCluster, int] | None = None
    stage_pools: dict[datetime, LiquidityPool] | None = None

    @classmethod
    def from_record(cls, record: LiquidityPoolRecord, *, ended_at: datetime | None) -> _Level:
        return cls(
            pool_id=record.pool_id,
            side=LiquiditySide(record.side),
            stages=list(_stages_of(record)),
            seed=record,
            ended_at=ended_at,
        )

    @property
    def stage(self) -> _Stage:
        return self.stages[-1]

    @property
    def swing_strength(self) -> SwingStrength:
        if self.external or any(s.strength is SwingStrength.EXTERNAL for s in self.stages):
            return SwingStrength.EXTERNAL

        return SwingStrength.INTERNAL

    @property
    def class_fallback(self) -> LiquidityClass:
        """§4.4's static label: a cluster or an external pivot is EXTERNAL."""
        if self.stage.source is PoolSource.CLUSTER:
            return LiquidityClass.EXTERNAL

        if self.seed is None:
            return LiquidityClass(self.swing_strength.value)

        if self.stage.strength is not None:
            return LiquidityClass(self.stage.strength.value)

        return LiquidityClass(self.seed.liquidity_class)

    def state_at(self, close_time: datetime) -> _Stage:
        """The pool once every change confirmed by `close_time` has applied."""
        position = bisect_right(self.stages, close_time, key=lambda stage: stage.starts)

        return self.stages[max(position - 1, 0)]

    def add_stage(self, stage: _Stage) -> bool:
        if any(existing.starts == stage.starts for existing in self.stages):
            return False

        self.stages.append(stage)
        self.stages.sort(key=lambda item: item.starts)
        self.stage_pools = None

        return True

    def pool_at(self, candle: Candle) -> LiquidityPool:
        """The pool this candle meets: the stage in force when it closed."""
        if self.stage_pools is None:
            self.stage_pools = {}

        stage = self.stages[_stage_index(self.stages, candle.close_time)]
        pool = self.stage_pools.get(stage.starts)

        if pool is None:
            pool = _stage_pool(self.pool_id, self.side, stage)
            self.stage_pools[stage.starts] = pool

        return pool


class _PoolMapWalk:
    """§4.2's pool map over one window, built in candle order.

    Every level is decided on the candle that confirms it, against the pools
    alive on that candle and with that candle's epsilon -- what the pass that
    first saw the candle had to go on -- so a later pass over the same candles
    decides the same (owner ruling 2026-09-14, audit M5/M10):

    * a swing is absorbed by the earliest-born live pool on its side whose band
      lies within epsilon of it, or it becomes a pool of its own, born on its
      first confirmation (a k=5 pivot is a k=2 pivot three candles sooner);
    * as each member of a cluster confirms, the cluster merges into the pool
      holding its first member's level -- §4.2's "merge into one pool with
      combined evidence" -- provided that pool is still alive. A consumed level
      stays consumed: "terminal states are permanent; no resurrection";
    * a pool is alive on a candle until the first candle that sweeps or breaks
      it, found by the same walk its lifecycle runs, stage by stage.
    """

    def __init__(
        self,
        candles: Sequence[Candle],
        atrs: Sequence[Decimal | None],
        *,
        seeds: Sequence[_Level],
        pool_id: Callable[..., str],
        decided_before: int = 0,
        known: frozenset[str] = frozenset(),
    ) -> None:
        self._candles = candles
        self._atrs = atrs
        self._pool_id = pool_id
        self._levels: dict[str, _Level] = {level.pool_id: level for level in seeds}
        self._holders: dict[tuple[int, SwingKind], str] = {}
        self._decided_before = decided_before
        self._known = known

    def run(
        self,
        internal_swings: Sequence[SwingPoint],
        external_swings: Sequence[SwingPoint],
        clusters: Sequence[EqualLevelCluster],
    ) -> tuple[_Level, ...]:
        pivots: dict[tuple[int, SwingKind], dict[SwingStrength, SwingPoint]] = {}

        for swing in (*internal_swings, *external_swings):
            pivots.setdefault((swing.index, swing.kind), {})[swing.strength] = swing

        confirmations: dict[tuple[int, SwingKind], int] = {}
        events: list[tuple[int, int, str, Decimal, str, Callable[[], None]]] = []

        for key, by_strength in pivots.items():
            first = min(by_strength.values(), key=_confirmation_of)
            confirmed = _confirmation_of(first)
            confirmations[key] = confirmed
            name = f"{first.index}:{first.kind.value}"
            side = _side_of(first).value

            events.append(
                (
                    confirmed,
                    0,
                    side,
                    first.price,
                    name,
                    partial(self._confirm, key, first, confirmed),
                )
            )

            external = by_strength.get(SwingStrength.EXTERNAL)

            if external is not None and external is not first:
                promoted = _confirmation_of(external)

                events.append(
                    (promoted, 1, side, first.price, name, partial(self._promote, key, promoted))
                )

        for cluster in clusters:
            kind = SwingKind.HIGH if cluster.side is LiquiditySide.BSL else SwingKind.LOW

            for count in range(2, cluster.member_count + 1):
                joined = confirmations.get((cluster.member_indices[count - 1], kind))

                if joined is None:
                    continue

                events.append(
                    (
                        joined,
                        2,
                        cluster.side.value,
                        cluster.extreme,
                        f"{cluster.cluster_id}:{count}",
                        partial(self._join, cluster, count, joined),
                    )
                )

        for *_, apply in sorted(events, key=lambda event: event[:5]):
            apply()

        return tuple(self._levels.values())

    def _confirm(self, key: tuple[int, SwingKind], swing: SwingPoint, confirmed: int) -> None:
        pool_id = self._pool_id(swing=swing)

        # A seed resting on this very pivot: the level is held already.
        if pool_id in self._levels:
            self._holders[key] = pool_id
            return

        side = _side_of(swing)
        holder = self._holder(side, swing.price, swing.price, confirmed)

        if holder is not None:
            self._holders[key] = holder.pool_id
            return

        # Audit M3: an earlier pass decided this candle and wrote no pool for
        # it, so none was born then. Deciding it again could only differ by the
        # ATR a later window start seeds -- zero on its first candles.
        if confirmed < self._decided_before and pool_id not in self._known:
            return

        self._levels[pool_id] = _Level(
            pool_id=pool_id,
            side=side,
            stages=[
                _Stage(
                    starts=self._candles[confirmed].close_time,
                    source=PoolSource.SWING,
                    strength=swing.strength,
                    price=swing.price,
                    band_low=swing.price,
                    band_high=swing.price,
                    member_count=1,
                )
            ],
            born=confirmed,
            swing=swing,
            judged_from=confirmed + 1,
        )
        self._holders[key] = pool_id

    def _promote(self, key: tuple[int, SwingKind], promoted: int) -> None:
        level = self._levels.get(self._holders.get(key, ""))

        # Only the pool this pivot created; an absorbed pivot changes nothing.
        if level is None or level.swing is None or (level.swing.index, level.swing.kind) != key:
            return

        level.external = True
        close = self._candles[promoted].close_time
        current = level.state_at(close)

        if current.source is PoolSource.SWING and current.strength is not SwingStrength.EXTERNAL:
            level.add_stage(replace(current, starts=close, strength=SwingStrength.EXTERNAL))

    def _join(self, cluster: EqualLevelCluster, count: int, joined: int) -> None:
        kind = SwingKind.HIGH if cluster.side is LiquiditySide.BSL else SwingKind.LOW
        level = self._levels.get(self._holders.get((cluster.member_indices[0], kind), ""))

        if level is None or not self._alive(level, joined):
            return

        close = self._candles[joined].close_time
        current = level.state_at(close)
        members = cluster.member_prices[:count]
        band_low = min(current.band_low, *members)
        band_high = max(current.band_high, *members)

        merged = _Stage(
            starts=close,
            source=PoolSource.CLUSTER,
            strength=None,
            price=band_high if cluster.side is LiquiditySide.BSL else band_low,
            band_low=band_low,
            band_high=band_high,
            member_count=max(current.member_count, count),
        )

        if level.add_stage(merged):
            level.merged = True

        level.cluster = (cluster, count)

    def _holder(
        self,
        side: LiquiditySide,
        low: Decimal,
        high: Decimal,
        index: int,
    ) -> _Level | None:
        """The earliest-born live pool on `side` holding [low, high], judged
        with the epsilon of the candle that confirms the newcomer (M5)."""
        epsilon = TOLERANCE_ATR * _atr_at(self._atrs, index)
        close = self._candles[index].close_time

        found = [
            level
            for level in self._levels.values()
            if level.side is side
            and _within(level.state_at(close), low, high, epsilon)
            and self._alive(level, index)
        ]

        return min(found, key=lambda level: (level.stages[0].starts, level.pool_id), default=None)

    def _alive(self, level: _Level, index: int) -> bool:
        close = self._candles[index].close_time
        age = int((close - level.stages[0].starts) // self._candles[index].timeframe.duration)

        if should_expire_pool(age_candles=age):
            return False

        if level.born is None:
            return level.ended_at is None or level.ended_at > close

        if level.ended_index is None and level.judged_from <= index:
            terminal, judged_from = _walk_to_terminal(
                level.pool_at, self._candles, self._atrs, level.judged_from, index
            )

            if terminal is not None:
                level.ended_index = terminal.index
            else:
                level.judged_from = judged_from

        return level.ended_index is None or level.ended_index > index


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
