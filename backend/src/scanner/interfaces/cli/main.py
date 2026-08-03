"""Ops CLI argument parsing (interface layer).

Pure parsing + input coercion — no infrastructure, no composition (layering law,
TAD §27). The composition root that wires adapters and runs commands is
`scanner.runtime.cli` (`python -m scanner.runtime.cli <command>`).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from scanner.shared import Timeframe


def parse_date(raw: str) -> datetime:
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scanner", description="Market data ops (Sprint S1)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync-symbols", help="Mirror the venue USDT spot registry (DDD T1)")

    backfill = sub.add_parser("backfill", help="Chunked, validated, idempotent candle backfill")
    backfill.add_argument("--symbol", required=True)
    backfill.add_argument("--timeframe", required=True, type=Timeframe.parse)
    backfill.add_argument("--start", required=True, type=parse_date, help="ISO date/datetime (UTC)")
    backfill.add_argument(
        "--end", type=parse_date, default=None, help="default: last closed boundary"
    )

    verify = sub.add_parser(
        "verify-continuity", help="Prove the stored record hole-free vs the incident ledger"
    )
    verify.add_argument("--symbol", required=True)
    verify.add_argument("--timeframe", required=True, type=Timeframe.parse)
    verify.add_argument("--start", required=True, type=parse_date)
    verify.add_argument("--end", required=True, type=parse_date)
    return parser
