"""Ops CLI argument parsing (interface layer).

Pure parsing + input coercion — no infrastructure, no composition.
Infrastructure wiring lives in scanner.runtime.cli.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from scanner.shared import Timeframe


def parse_date(
    raw: str,
) -> datetime:
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


def _add_structure_range_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--symbol",
        required=True,
    )

    parser.add_argument(
        "--timeframe",
        required=True,
        type=Timeframe.parse,
    )

    parser.add_argument(
        "--start",
        required=True,
        type=parse_date,
        help="ISO date/datetime UTC",
    )

    parser.add_argument(
        "--end",
        required=True,
        type=parse_date,
        help="ISO date/datetime UTC",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scanner",
        description="Crypto scanner operations",
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser(
        "sync-symbols",
        help="Mirror venue USDT spot registry",
    )

    backfill = sub.add_parser(
        "backfill",
        help="Chunked validated candle backfill",
    )

    backfill.add_argument(
        "--symbol",
        required=True,
    )

    backfill.add_argument(
        "--timeframe",
        required=True,
        type=Timeframe.parse,
    )

    backfill.add_argument(
        "--start",
        required=True,
        type=parse_date,
        help="ISO date/datetime UTC",
    )

    backfill.add_argument(
        "--end",
        type=parse_date,
        default=None,
        help="default: last closed boundary",
    )

    verify = sub.add_parser(
        "verify-continuity",
        help="Verify stored candle continuity",
    )

    verify.add_argument(
        "--symbol",
        required=True,
    )

    verify.add_argument(
        "--timeframe",
        required=True,
        type=Timeframe.parse,
    )

    verify.add_argument(
        "--start",
        required=True,
        type=parse_date,
    )

    verify.add_argument(
        "--end",
        required=True,
        type=parse_date,
    )

    warmth = sub.add_parser(
        "warmth",
        help="Report which contexts can produce detections (SLS 1.9)",
    )

    warmth.add_argument(
        "--symbol",
        default=None,
        help="default: every active symbol in the registry",
    )

    warmth.add_argument(
        "--timeframe",
        default=None,
        type=Timeframe.parse,
        help="default: every timeframe the engine consumes",
    )

    engine = sub.add_parser(
        "engine",
        help="Detection engine operations",
    )

    engine_sub = engine.add_subparsers(
        dest="engine_command",
        required=True,
    )

    engine_run = engine_sub.add_parser(
        "run",
        help="Replay structure engine over stored candles",
    )

    _add_structure_range_arguments(engine_run)

    rebuild_state = engine_sub.add_parser(
        "rebuild-state",
        help=("Discard structure snapshot and rebuild from stored candle history"),
    )

    _add_structure_range_arguments(rebuild_state)

    rank = sub.add_parser(
        "rank",
        help="Ranking engine operations (SLS §9)",
    )

    rank_sub = rank.add_subparsers(
        dest="rank_command",
        required=True,
    )

    rank_snapshot = rank_sub.add_parser(
        "snapshot",
        help="Order the published setups recorded at one close",
    )

    rank_snapshot.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated exchange symbols to consider",
    )

    rank_snapshot.add_argument(
        "--timeframe",
        required=True,
        type=Timeframe.parse,
    )

    rank_snapshot.add_argument(
        "--at",
        required=True,
        type=parse_date,
        help="Candle open time, ISO date/datetime UTC",
    )

    rank_snapshot.add_argument(
        "--elapsed-candles",
        type=int,
        default=0,
        help="Closed candles since publication, for §9.3's display decay",
    )

    signals = sub.add_parser(
        "signals",
        help="Published signal record operations (SLS §15, DDD T17)",
    )

    signals_sub = signals.add_subparsers(
        dest="signals_command",
        required=True,
    )

    tail = signals_sub.add_parser(
        "tail",
        help="The most recently published signals and their current state",
    )

    tail.add_argument(
        "--symbol",
        default=None,
        help="default: every symbol",
    )

    tail.add_argument(
        "--timeframe",
        default=None,
        type=Timeframe.parse,
        help="default: every timeframe",
    )

    tail.add_argument(
        "--limit",
        type=int,
        default=20,
    )

    signals_sub.add_parser(
        "verify-hashes",
        help="Recompute every T17 payload seal (SLS §15.3.5)",
    )

    return parser
