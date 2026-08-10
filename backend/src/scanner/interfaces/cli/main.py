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
    return datetime.fromisoformat(
        raw
    ).replace(
        tzinfo=UTC
    )


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

    _add_structure_range_arguments(
        engine_run
    )

    rebuild_state = engine_sub.add_parser(
        "rebuild-state",
        help=(
            "Discard structure snapshot and rebuild "
            "from stored candle history"
        ),
    )

    _add_structure_range_arguments(
        rebuild_state
    )

    return parser
