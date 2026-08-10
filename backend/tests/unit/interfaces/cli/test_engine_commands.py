"""Tests for Sprint S4 engine CLI parsing."""

from __future__ import annotations

from scanner.interfaces.cli.main import (
    build_parser,
)
from scanner.shared import Timeframe


def test_engine_run_command_parses() -> None:
    args = build_parser().parse_args(
        [
            "engine",
            "run",
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "H1",
            "--start",
            "2026-08-01",
            "--end",
            "2026-08-02",
        ]
    )

    assert args.command == "engine"
    assert args.engine_command == "run"
    assert args.symbol == "BTCUSDT"
    assert args.timeframe is Timeframe.H1


def test_engine_rebuild_state_command_parses() -> None:
    args = build_parser().parse_args(
        [
            "engine",
            "rebuild-state",
            "--symbol",
            "ETHUSDT",
            "--timeframe",
            "H4",
            "--start",
            "2026-08-01",
            "--end",
            "2026-08-10",
        ]
    )

    assert args.command == "engine"
    assert (
        args.engine_command
        == "rebuild-state"
    )
    assert args.symbol == "ETHUSDT"
    assert args.timeframe is Timeframe.H4
