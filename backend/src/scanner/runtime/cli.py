"""Ops CLI composition root: `python -m scanner.runtime.cli <command>`.

The only place the CLI touches infrastructure (layering law, TAD §27): it wires
adapters to application services, runs the command, and renders the report.
Parsing lives in the interface layer (`scanner.interfaces.cli.main`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from scanner.application.marketdata import BackfillService, SymbolSyncService, verify_continuity
from scanner.config import load_ingest_settings
from scanner.infrastructure.exchanges.binance import BinanceRestAdapter, RateBudget
from scanner.infrastructure.persistence.database import build_engine, build_session_factory
from scanner.infrastructure.persistence.repositories import (
    PgCandleRepository,
    PgIncidentRepository,
    PgSymbolRepository,
)
from scanner.interfaces.cli.main import build_parser


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def _run_sync(args: argparse.Namespace) -> int:
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
                provider, PgSymbolRepository(sessions), SystemClock()
            ).sync()
        print(
            f"sync-symbols: seen={report.seen} "
            f"eligible_usdt={report.eligible} upserted={report.upserted}"
        )
        return 0
    finally:
        await engine.dispose()


async def _run_backfill(args: argparse.Namespace) -> int:
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
                provider, PgCandleRepository(sessions, clock), PgIncidentRepository(sessions), clock
            )
            report = await service.backfill(args.symbol, args.timeframe, args.start, args.end)
        print(
            f"backfill {report.symbol} {report.timeframe.value}: "
            f"range=[{report.requested_start.isoformat()}, {report.requested_end.isoformat()}) "
            f"fetched={report.fetched} inserted={report.inserted} "
            f"gaps_recorded={report.gaps_recorded} quarantined={report.quarantined_batches}"
            + (f" resumed_from={report.resumed_from.isoformat()}" if report.resumed_from else "")
        )
        for line in report.findings:
            print(f"  finding: {line}")
        return 0 if report.quarantined_batches == 0 else 2
    finally:
        await engine.dispose()


async def _run_verify(args: argparse.Namespace) -> int:
    settings = load_ingest_settings()
    engine = build_engine(settings.db_dsn)
    try:
        sessions = build_session_factory(engine)
        report = await verify_continuity(
            PgCandleRepository(sessions, SystemClock()),
            PgIncidentRepository(sessions),
            args.symbol,
            args.timeframe,
            args.start,
            args.end,
        )
        print(
            f"verify-continuity {report.symbol} {report.timeframe.value}: "
            f"expected={report.expected} present={report.present} missing={report.missing} "
            f"covered_by_incidents={report.covered_by_incidents} uncovered={len(report.uncovered)}"
        )
        for ts in report.uncovered[:20]:
            print(f"  UNCOVERED HOLE: {ts.isoformat()}")
        return 0 if report.ok else 2
    finally:
        await engine.dispose()


_HANDLERS: dict[str, Callable[[argparse.Namespace], Awaitable[int]]] = {
    "sync-symbols": _run_sync,
    "backfill": _run_backfill,
    "verify-continuity": _run_verify,
}


async def _dispatch(args: argparse.Namespace) -> int:
    return await _HANDLERS[args.command](args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
