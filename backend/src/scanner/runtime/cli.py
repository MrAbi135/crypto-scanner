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
import redis.asyncio as aioredis

from scanner.application.detection.state import (
    EngineStateManager,
)
from scanner.application.detection.structure_replay import (
    StructureReplayService,
)
from scanner.application.marketdata import (
    BackfillService,
    SymbolSyncService,
    verify_continuity,
)
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
from scanner.infrastructure.persistence.detection_repositories import (
    PgEngineEventRepository,
)
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.infrastructure.redis.engine_state import (
    RedisEngineStateStore,
)
from scanner.interfaces.cli.main import build_parser


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def _run_sync(
    args: argparse.Namespace,
) -> int:
    settings = load_ingest_settings()

    engine = build_engine(
        settings.db_dsn
    )

    try:
        sessions = build_session_factory(
            engine
        )

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:
            provider = BinanceRestAdapter(
                client,
                RateBudget(
                    settings.binance_weight_capacity
                ),
                base_url=settings.binance_base_url,
            )

            report = await SymbolSyncService(
                provider,
                PgSymbolRepository(sessions),
                SystemClock(),
            ).sync()

        print(
            f"sync-symbols: seen={report.seen} "
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

    engine = build_engine(
        settings.db_dsn
    )

    try:
        sessions = build_session_factory(
            engine
        )

        clock = SystemClock()

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:
            provider = BinanceRestAdapter(
                client,
                RateBudget(
                    settings.binance_weight_capacity
                ),
                base_url=settings.binance_base_url,
            )

            service = BackfillService(
                provider,
                PgCandleRepository(
                    sessions,
                    clock,
                ),
                PgIncidentRepository(
                    sessions
                ),
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
            f"gaps_recorded={report.gaps_recorded} "
            f"quarantined="
            f"{report.quarantined_batches}"
            + (
                f" resumed_from="
                f"{report.resumed_from.isoformat()}"
                if report.resumed_from
                else ""
            )
        )

        for line in report.findings:
            print(
                f"  finding: {line}"
            )

        return (
            0
            if report.quarantined_batches == 0
            else 2
        )

    finally:
        await engine.dispose()


async def _run_verify(
    args: argparse.Namespace,
) -> int:
    settings = load_ingest_settings()

    engine = build_engine(
        settings.db_dsn
    )

    try:
        sessions = build_session_factory(
            engine
        )

        report = await verify_continuity(
            PgCandleRepository(
                sessions,
                SystemClock(),
            ),
            PgIncidentRepository(
                sessions
            ),
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

        for timestamp in report.uncovered[
            :20
        ]:
            print(
                "  UNCOVERED HOLE: "
                f"{timestamp.isoformat()}"
            )

        return (
            0
            if report.ok
            else 2
        )

    finally:
        await engine.dispose()


async def _run_engine(
    args: argparse.Namespace,
    *,
    rebuild_state: bool,
) -> int:
    settings = get_settings(
        "engine"
    )

    engine = build_engine(
        settings.db_dsn
    )

    redis_client = aioredis.from_url(
        settings.redis_url
    )

    try:
        sessions = build_session_factory(
            engine
        )

        clock = SystemClock()

        candle_repo = PgCandleRepository(
            sessions,
            clock,
        )

        event_repo = PgEngineEventRepository(
            sessions
        )

        state_store = RedisEngineStateStore(
            redis_client
        )

        state_manager = EngineStateManager(
            state_store
        )

        service = StructureReplayService(
            candle_repo,
            event_repo,
            state_manager,
            clock,
        )

        report = await service.run(
            args.symbol,
            args.timeframe,
            args.start,
            args.end,
            rebuild_state=rebuild_state,
        )

        operation = (
            "engine rebuild-state"
            if rebuild_state
            else "engine run"
        )

        print(
            f"{operation} "
            f"{report.symbol} "
            f"{report.timeframe.value}: "
            f"candles={report.candles} "
            f"internal_swings="
            f"{report.internal_swings} "
            f"external_swings="
            f"{report.external_swings} "
            f"classified="
            f"{report.classified_events} "
            f"events_inserted="
            f"{report.events_inserted} "
            f"trend={report.trend_state}"
        )

        if (
            report.last_processed_open_time
            is not None
        ):
            print(
                "  last_processed="
                f"{report.last_processed_open_time.isoformat()}"
            )

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
}


async def _dispatch(
    args: argparse.Namespace,
) -> int:
    if args.command == "engine":
        if args.engine_command == "run":
            return await _run_engine_run(
                args
            )

        if (
            args.engine_command
            == "rebuild-state"
        ):
            return await _run_engine_rebuild(
                args
            )

        raise ValueError(
            "unknown engine command: "
            f"{args.engine_command}"
        )

    return await _HANDLERS[
        args.command
    ](
        args
    )


def main(
    argv: list[str] | None = None,
) -> int:
    args = build_parser().parse_args(
        argv
    )

    return asyncio.run(
        _dispatch(args)
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )
