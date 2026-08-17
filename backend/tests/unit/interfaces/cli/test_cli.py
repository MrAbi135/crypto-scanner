"""CLI parsing + dispatch tests (S0.3 — CLI composition-root refactor)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest

from scanner.interfaces.cli.main import build_parser, parse_date
from scanner.runtime import cli
from scanner.shared import Timeframe


def test_parse_date_is_utc() -> None:
    assert parse_date("2024-01-01") == datetime(2024, 1, 1, tzinfo=UTC)


def test_backfill_parses_all_fields() -> None:
    args = build_parser().parse_args(
        ["backfill", "--symbol", "BTCUSDT", "--timeframe", "H1", "--start", "2024-01-01"]
    )
    assert args.command == "backfill"
    assert args.symbol == "BTCUSDT"
    assert args.timeframe == Timeframe.H1
    assert args.start == datetime(2024, 1, 1, tzinfo=UTC)
    assert args.end is None


def test_sync_command() -> None:
    assert build_parser().parse_args(["sync-symbols"]).command == "sync-symbols"


def test_verify_requires_end() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["verify-continuity", "--symbol", "BTC", "--timeframe", "H1", "--start", "2024-01-01"]
        )


def test_missing_command_exits() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_all_commands_have_handlers() -> None:
    assert set(cli._HANDLERS) == {
        "sync-symbols",
        "backfill",
        "verify-continuity",
        "warmth",
    }


async def test_dispatch_routes_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def fake(args: argparse.Namespace) -> int:
        seen["command"] = args.command
        return 7

    monkeypatch.setitem(cli._HANDLERS, "sync-symbols", fake)
    args = build_parser().parse_args(["sync-symbols"])
    assert await cli._dispatch(args) == 7
    assert seen["command"] == "sync-symbols"
