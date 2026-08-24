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


def test_signals_tail_defaults_to_every_series() -> None:
    args = build_parser().parse_args(["signals", "tail"])

    assert (args.command, args.signals_command) == ("signals", "tail")
    # None rather than a sentinel symbol: the repository turns each one into
    # an absent WHERE clause, so "no filter" and "filter on nothing" cannot be
    # confused.
    assert (args.symbol, args.timeframe) == (None, None)
    assert args.limit == 20


def test_signals_tail_takes_a_series_and_a_limit() -> None:
    args = build_parser().parse_args(
        ["signals", "tail", "--symbol", "BTCUSDT", "--timeframe", "H4", "--limit", "5"]
    )

    assert (args.symbol, args.timeframe, args.limit) == ("BTCUSDT", Timeframe.H4, 5)


def test_signals_verify_hashes_takes_no_arguments() -> None:
    args = build_parser().parse_args(["signals", "verify-hashes"])

    assert args.signals_command == "verify-hashes"


def test_signals_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["signals"])


async def test_dispatch_refuses_an_unknown_signals_subcommand() -> None:
    """The dispatch arm has to fail loudly rather than fall through.

    `_HANDLERS[args.command]` at the bottom would raise a bare KeyError for a
    subcommand the parser accepted and the dispatcher forgot -- a real risk
    while both grow.
    """
    args = build_parser().parse_args(["signals", "tail"])
    args.signals_command = "not-a-command"

    with pytest.raises(ValueError, match="unknown signals command"):
        await cli._dispatch(args)


def test_users_create_takes_an_email_and_a_password_env_var() -> None:
    """No `--password`.

    argv is visible in `ps`, lands in shell history, and is captured by
    process accounting. The password comes from the environment or a prompt.
    """
    args = build_parser().parse_args(["users", "create", "--email", "ops@example.com"])

    assert (args.command, args.users_command) == ("users", "create")
    assert args.email == "ops@example.com"
    assert args.password_env == "SCANNER_NEW_PASSWORD"
    assert not hasattr(args, "password")


def test_users_create_requires_an_email() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["users", "create"])


def test_users_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["users"])


async def test_dispatch_refuses_an_unknown_users_subcommand() -> None:
    args = build_parser().parse_args(["users", "list"])
    args.users_command = "not-a-command"

    with pytest.raises(ValueError, match="unknown users command"):
        await cli._dispatch(args)
