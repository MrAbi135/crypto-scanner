"""The published-timeframe setting (owner ruling 2026-09-15: H1 and H4 for now)."""

from __future__ import annotations

import os

import pytest

from scanner.application.marketdata.contexts import parse_signal_timeframes
from scanner.config import get_settings
from scanner.shared import Timeframe
from scanner.shared.errors import ValidationError

_REQUIRED = {
    "SCANNER_ENV": "dev",
    "SCANNER_DB_DSN": "postgresql+asyncpg://u:p@h:5432/d",
    "SCANNER_REDIS_URL": "redis://h:6379/0",
}


@pytest.fixture
def scanner_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in list(os.environ):
        if key.startswith("SCANNER_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_the_engine_publishes_h1_and_h4_unless_told_otherwise(
    scanner_env: pytest.MonkeyPatch,
) -> None:
    settings = get_settings("engine")

    assert parse_signal_timeframes(settings.signal_timeframes) == {Timeframe.H1, Timeframe.H4}


def test_an_operator_can_widen_the_published_set(scanner_env: pytest.MonkeyPatch) -> None:
    scanner_env.setenv("SCANNER_SIGNAL_TIMEFRAMES", "M15,H1,H4")

    settings = get_settings("engine")

    assert parse_signal_timeframes(settings.signal_timeframes) == {
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
    }


def test_a_published_set_may_skip_rungs_of_the_ingest_ladder() -> None:
    """H1 and H4 publish while M15 is ingested only for H1's zone confirmation;
    the ingest ladder's no-hole rule is not a publishing rule."""
    assert parse_signal_timeframes("H1, H4") == {Timeframe.H1, Timeframe.H4}


@pytest.mark.parametrize("raw", ["", " , ", "H1,H1"])
def test_an_empty_or_repeated_set_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_signal_timeframes(raw)
