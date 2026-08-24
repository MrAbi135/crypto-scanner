"""Ops CLI composition root.

Usage:
    python -m scanner.runtime.cli <command>

Parsing stays in scanner.interfaces.cli.main. Infrastructure wiring belongs
here only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from scanner.application.marketdata import (
    BackfillService,
    SymbolSyncService,
    verify_continuity,
)
from scanner.application.marketdata.warmth import ENGINE_TIMEFRAMES, assess_all
from scanner.application.ranking import RankingSnapshotService
from scanner.application.signal_audit import verify_seals
from scanner.config import (
    get_settings,
    load_ingest_settings,
)
from scanner.infrastructure.exchanges.binance import (
    BinanceRestAdapter,
    RateBudget,
)
from scanner.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.infrastructure.persistence.setup_repository import PgSetupRepository
from scanner.infrastructure.persistence.signal_repository import PgSignalRepository
from scanner.infrastructure.persistence.signal_transition_repository import (
    PgSignalTransitionRepository,
)
from scanner.infrastructure.redis.client import build_redis
from scanner.interfaces.cli.main import build_parser
from scanner.runtime.wiring.detection import build_detection_pipeline


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def _run_sync(
    args: argparse.Namespace,
) -> int:
    settings = load_ingest_settings()
    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)

        async with httpx.AsyncClient(timeout=30.0) as client:
            provider = BinanceRestAdapter(
                client,
                RateBudget(settings.binance_weight_capacity),
                base_url=settings.binance_base_url,
            )

            report = await SymbolSyncService(
                provider,
                PgSymbolRepository(sessions),
                SystemClock(),
            ).sync()

        print(
            f"sync-symbols: "
            f"seen={report.seen} "
            f"eligible_usdt={report.eligible} "
            f"upserted={report.upserted}"
        )

        return 0

    finally:
        await engine.dispose()


async def _run_backfill(
    args: argparse.Namespace,
) -> int:
    settings = load_ingest_settings()
    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)

        clock = SystemClock()

        async with httpx.AsyncClient(timeout=30.0) as client:
            provider = BinanceRestAdapter(
                client,
                RateBudget(settings.binance_weight_capacity),
                base_url=settings.binance_base_url,
            )

            service = BackfillService(
                provider,
                PgCandleRepository(
                    sessions,
                    clock,
                ),
                PgIncidentRepository(sessions),
                clock,
            )

            report = await service.backfill(
                args.symbol,
                args.timeframe,
                args.start,
                args.end,
            )

        print(
            f"backfill {report.symbol} "
            f"{report.timeframe.value}: "
            f"range=["
            f"{report.requested_start.isoformat()}, "
            f"{report.requested_end.isoformat()}) "
            f"fetched={report.fetched} "
            f"inserted={report.inserted} "
            f"gaps_recorded="
            f"{report.gaps_recorded} "
            f"quarantined="
            f"{report.quarantined_batches}"
            + (f" resumed_from={report.resumed_from.isoformat()}" if report.resumed_from else "")
        )

        for line in report.findings:
            print(f"  finding: {line}")

        return 0 if report.quarantined_batches == 0 else 2

    finally:
        await engine.dispose()


async def _run_verify(
    args: argparse.Namespace,
) -> int:
    settings = load_ingest_settings()
    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)

        report = await verify_continuity(
            PgCandleRepository(
                sessions,
                SystemClock(),
            ),
            PgIncidentRepository(sessions),
            args.symbol,
            args.timeframe,
            args.start,
            args.end,
        )

        print(
            f"verify-continuity "
            f"{report.symbol} "
            f"{report.timeframe.value}: "
            f"expected={report.expected} "
            f"present={report.present} "
            f"missing={report.missing} "
            f"covered_by_incidents="
            f"{report.covered_by_incidents} "
            f"uncovered="
            f"{len(report.uncovered)}"
        )

        for timestamp in report.uncovered[:20]:
            print(f"  UNCOVERED HOLE: {timestamp.isoformat()}")

        return 0 if report.ok else 2

    finally:
        await engine.dispose()


async def _run_warmth(
    args: argparse.Namespace,
) -> int:
    """Answer "which contexts can produce detections", in one screen.

    Defaults to the whole active registry because the failure this exists to
    catch -- everything cold, silently -- is invisible when you inspect the one
    symbol you already suspected.
    """
    settings = load_ingest_settings()
    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)
        clock = SystemClock()

        candles = PgCandleRepository(sessions, clock)

        symbols: tuple[str, ...]

        if args.symbol:
            symbols = (args.symbol,)
        else:
            registry = await PgSymbolRepository(sessions).list_active()
            symbols = tuple(symbol.exchange_symbol for symbol in registry)

        if not symbols:
            # Not an empty result -- an unrun prerequisite. The registry being
            # empty is exactly the state S3b exists to end, and reporting it as
            # "0 contexts, all fine" is how it stayed unnoticed this long.
            print("warmth: the symbol registry is empty -- run `sync-symbols` first")

            return 1

        timeframes = (args.timeframe,) if args.timeframe else ENGINE_TIMEFRAMES

        contexts = tuple((symbol, tf) for symbol in symbols for tf in timeframes)

        reports = await assess_all(candles, contexts, now=clock.now())

        warm = 0

        for report in reports:
            if report.detection_warm:
                warm += 1

            print(
                f"  {report.symbol:<12} {report.timeframe.value:<4} "
                f"candles={report.closed_candles:<6} {report.describe()}"
            )

        print(f"warmth: {warm}/{len(reports)} contexts can produce detections")

        return 0 if warm else 1
    finally:
        await engine.dispose()


async def _run_engine(
    args: argparse.Namespace,
    *,
    rebuild_state: bool,
) -> int:
    settings = get_settings("engine")

    engine = build_engine(settings.db_dsn)

    redis_client = build_redis(settings.redis_url)

    try:
        sessions = build_session_factory(engine)

        pipeline = build_detection_pipeline(
            sessions,
            redis_client,
            SystemClock(),
        )

        report = await pipeline.run(
            args.symbol,
            args.timeframe,
            args.start,
            args.end,
            rebuild_state=rebuild_state,
        )

        structure_report = report.structure
        liquidity_report = report.liquidity
        structure_shift_report = report.structure_shift
        ict_report = report.ict
        ict_ote_report = report.ict_ote
        ict_ob_report = report.ict_ob
        ict_interaction_report = report.ict_interaction
        participation_report = report.participation
        confluence_report = report.confluence

        operation = "engine rebuild-state" if rebuild_state else "engine run"

        print(
            f"{operation} "
            f"{structure_report.symbol} "
            f"{structure_report.timeframe.value}: "
            f"candles="
            f"{structure_report.candles} "
            f"internal_swings="
            f"{structure_report.internal_swings} "
            f"external_swings="
            f"{structure_report.external_swings} "
            f"classified="
            f"{structure_report.classified_events} "
            f"events_inserted="
            f"{structure_report.events_inserted} "
            f"trend="
            f"{structure_shift_report.trend_state}"
        )

        print(
            "  liquidity: "
            f"internal_pools="
            f"{liquidity_report.internal_pools} "
            f"external_pools="
            f"{liquidity_report.external_pools} "
            f"clusters="
            f"{liquidity_report.clusters} "
            f"clustered_swings="
            f"{liquidity_report.clustered_swings} "
            f"upserted="
            f"{liquidity_report.pools_upserted} "
            f"active="
            f"{liquidity_report.active_pools} "
            f"sweeps="
            f"{liquidity_report.sweeps} "
            f"broken="
            f"{liquidity_report.broken_pools} "
            f"expired="
            f"{liquidity_report.expired_pools}"
        )

        print(
            "  structure_shift: "
            f"choch_created="
            f"{structure_shift_report.choch_created} "
            f"mss_created="
            f"{structure_shift_report.mss_created} "
            f"failed_candidates="
            f"{structure_shift_report.failed_candidates} "
            f"events_inserted="
            f"{structure_shift_report.events_inserted} "
            f"trend="
            f"{structure_shift_report.trend_state}"
        )

        print(
            "  ict: "
            f"displacements="
            f"{ict_report.displacements} "
            f"fvgs="
            f"{ict_report.fvgs_detected} "
            f"ifvgs="
            f"{ict_report.ifvgs_created} "
            f"bprs="
            f"{ict_report.bprs_created} "
            f"upserted="
            f"{ict_report.zones_upserted} "
            f"transitions="
            f"{ict_report.transitions} "
            f"live_zones="
            f"{ict_report.live_zones}"
        )

        print(
            "  ict_ote: "
            f"dealing_ranges="
            f"{ict_ote_report.dealing_ranges} "
            f"impulse_legs="
            f"{ict_ote_report.impulse_legs} "
            f"otes_detected="
            f"{ict_ote_report.otes_detected} "
            f"upserted="
            f"{ict_ote_report.zones_upserted} "
            f"transitions="
            f"{ict_ote_report.transitions} "
            f"live_otes="
            f"{ict_ote_report.live_otes}"
        )

        print(
            "  ict_ob: "
            f"displacements="
            f"{ict_ob_report.displacements} "
            f"detected="
            f"{ict_ob_report.order_blocks_detected} "
            f"upserted="
            f"{ict_ob_report.order_blocks_upserted} "
            f"breakers_created="
            f"{ict_ob_report.breakers_created} "
            f"mitigations_created="
            f"{ict_ob_report.mitigations_created} "
            f"transitions="
            f"{ict_ob_report.transitions} "
            f"live_obs="
            f"{ict_ob_report.live_order_blocks} "
            f"live_breakers="
            f"{ict_ob_report.live_breakers} "
            f"live_mitigations="
            f"{ict_ob_report.live_mitigations}"
        )

        print(
            "  ict_interactions: "
            f"zones={ict_interaction_report.zones_evaluated} "
            f"touches={ict_interaction_report.touches} "
            f"rejections={ict_interaction_report.rejections} "
            f"mitigations={ict_interaction_report.mitigations} "
            f"respects={ict_interaction_report.respects} "
            f"violations={ict_interaction_report.violations} "
            f"confirmations={ict_interaction_report.confirmations} "
            f"inserted={ict_interaction_report.interactions_inserted}"
        )

        # Printed because its absence is what hid whether the service ran at
        # all: the pipeline called it, the CLI said nothing, and the only way
        # to find out was to query the database.
        print(
            "  participation: "
            f"spikes={participation_report.volume_spikes} "
            f"vol_expansion={participation_report.expansions} "
            f"vol_contraction={participation_report.contractions} "
            f"range_expansion={participation_report.range_expansions} "
            f"compression={participation_report.compressions} "
            f"accelerating={participation_report.accelerations} "
            f"exhaustion={participation_report.exhaustion_watches} "
            f"inserted={participation_report.events_inserted}"
        )

        for candidate in confluence_report.candidates:
            if candidate.gates_passed:
                print(
                    f"  confluence {candidate.direction}: "
                    f"confidence={candidate.confidence} "
                    f"grade={candidate.grade} "
                    f"archetype={candidate.archetype} "
                    f"publishable={candidate.publishable}"
                )
            else:
                # A blocked direction prints why. "No candidate" and "gate G4
                # found no zone" are the same silence otherwise, and only one
                # of them is a reason to go looking for a bug.
                print(
                    f"  confluence {candidate.direction}: "
                    f"blocked_by={','.join(candidate.failed_gates)}"
                )

        # Printed because "the HTF is ranging" and "nobody looked" are the
        # same F6 score, and only the second is a reason to check the ladder.
        print(f"  confluence htf: {confluence_report.htf_state or 'unread'}")

        if confluence_report.unreachable:
            print(f"  confluence unreachable: {','.join(confluence_report.unreachable)}")

        if structure_report.last_processed_open_time is not None:
            print(f"  last_processed={structure_report.last_processed_open_time.isoformat()}")

        return 0

    finally:
        await redis_client.aclose()
        await engine.dispose()


async def _run_engine_run(
    args: argparse.Namespace,
) -> int:
    return await _run_engine(
        args,
        rebuild_state=False,
    )


async def _run_engine_rebuild(
    args: argparse.Namespace,
) -> int:
    return await _run_engine(
        args,
        rebuild_state=True,
    )


async def _run_rank_snapshot(
    args: argparse.Namespace,
) -> int:
    """§9.2 over the candidates recorded at one close."""
    settings = get_settings("engine")

    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)

        service = RankingSnapshotService(
            PgSetupRepository(sessions),
            PgSymbolRepository(sessions),
        )

        snapshot = await service.snapshot(
            tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            args.timeframe,
            args.at,
            elapsed_candles=args.elapsed_candles,
        )

        # The counts come first and are printed even when nothing published.
        # A board that showed only its rows would make a quiet market and a
        # broken pipeline look the same, and that distinction is the only
        # reason to run this by hand.
        print(
            f"timeframe={snapshot.timeframe.value} at={snapshot.at.isoformat()} "
            f"gate_passers={snapshot.gate_passers} published={len(snapshot.rows)} "
            f"below_floor={snapshot.below_floor}"
        )

        for row in snapshot.rows:
            setup = row.setup

            print(
                f"{row.position:>3}  {setup.symbol:<14} {setup.direction:<4} "
                f"{setup.archetype.value:<3} tier={setup.tier.value:<10} "
                f"confidence={setup.confidence} display={row.display}"
            )

        return 0
    finally:
        await engine.dispose()


async def _run_signals_tail(
    args: argparse.Namespace,
) -> int:
    """The published record as an operator reads it: newest first, with state.

    The state comes from T18 rather than a column on T17, one query per row.
    That is N+1, and deliberately so at a default limit of 20 -- the
    alternative is a window function over the whole transition history to
    save nineteen index lookups on a command a human runs by hand.
    """
    settings = get_settings("engine")

    engine = build_engine(settings.db_dsn)

    try:
        sessions = build_session_factory(engine)

        signals = PgSignalRepository(sessions)
        transitions = PgSignalTransitionRepository(sessions)

        rows = await signals.recent(
            limit=args.limit,
            symbol=args.symbol,
            timeframe=args.timeframe,
        )

        # Printed even when empty, and saying so. A silent command makes "no
        # signals published" and "wrong database" look identical, which is
        # the question this is usually run to answer.
        print(f"signals: {len(rows)} (limit={args.limit})")

        for row in rows:
            state = await transitions.current_state(row.signal_id) or "NO HISTORY"

            print(
                f"{row.published_at.isoformat()}  {row.symbol:<12} "
                f"{row.timeframe.value:<4} {row.direction:<4} {row.archetype:<3} "
                f"{row.grade:<1} conf={row.final_confidence:<6} "
                f"entry={row.entry_proximal}/{row.entry_distal} "
                f"inval={row.invalidation_level} {state}"
            )

        return 0
    finally:
        await engine.dispose()


async def _run_signals_verify_hashes(
    args: argparse.Namespace,
) -> int:
    """§15.3(5)'s seals, recomputed.

    Exits non-zero on any failure. This is meant to run from the backup cycle
    -- DDD says T17 is "checksum-verified in every backup cycle" -- and a
    verification that reports a problem on stdout and exits 0 is one nobody
    finds out about.
    """
    settings = get_settings("engine")

    engine = build_engine(settings.db_dsn)

    try:
        report = await verify_seals(PgSignalRepository(build_session_factory(engine)))

        print(f"verify-hashes: checked={report.checked} failures={len(report.failures)}")

        for failure in report.failures:
            print(
                f"  {failure.signal_id}: {failure.reason} "
                f"recorded={failure.recorded[:16]}... "
                f"recomputed={failure.recomputed[:16]}..."
            )

        if not report.intact:
            return 1

        # Said out loud, because "checked=0 failures=0" also prints a clean
        # line and means something entirely different.
        if report.checked == 0:
            print("verify-hashes: no signals on this database -- nothing was verified")

        return 0
    finally:
        await engine.dispose()


_HANDLERS: dict[
    str,
    Callable[
        [argparse.Namespace],
        Awaitable[int],
    ],
] = {
    "sync-symbols": _run_sync,
    "backfill": _run_backfill,
    "verify-continuity": _run_verify,
    "warmth": _run_warmth,
}


async def _dispatch(
    args: argparse.Namespace,
) -> int:
    if args.command == "rank":
        if args.rank_command == "snapshot":
            return await _run_rank_snapshot(args)

        raise ValueError(f"unknown rank command: {args.rank_command}")

    if args.command == "signals":
        if args.signals_command == "tail":
            return await _run_signals_tail(args)

        if args.signals_command == "verify-hashes":
            return await _run_signals_verify_hashes(args)

        raise ValueError(f"unknown signals command: {args.signals_command}")

    if args.command == "engine":
        if args.engine_command == "run":
            return await _run_engine_run(args)

        if args.engine_command == "rebuild-state":
            return await _run_engine_rebuild(args)

        raise ValueError(f"unknown engine command: {args.engine_command}")

    return await _HANDLERS[args.command](args)


def main(
    argv: list[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
