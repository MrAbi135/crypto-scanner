"""The published-timeframe setting, and the ingest ladder it is drawn from.

Owner ruling 2026-10-05 with SLS v1.0.14: all four scanned timeframes publish,
and D1 joins the ingest ladder so H4's F6 has a rung above it to read.
"""

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


def test_the_engine_publishes_every_scanned_timeframe_unless_told_otherwise(
    scanner_env: pytest.MonkeyPatch,
) -> None:
    """Owner ruling 2026-10-05, with SLS v1.0.14. Held at H1,H4 until then."""
    settings = get_settings("engine")

    assert parse_signal_timeframes(settings.signal_timeframes) == {
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
    }


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


def test_the_engine_ingests_every_scanned_timeframe_including_d1(
    scanner_env: pytest.MonkeyPatch,
) -> None:
    """D1 is ingested but never published.

    H4's F6 reads the trend of the rung above it, which §0.3's chain says is
    D1; without D1 ingested that read has nothing to find and F6 sat pinned at
    the neutral 50 on every H4 candidate. So D1 has to be scanned -- and it must
    NOT be in the published set, because §0.3 gives it the bias role and keeps
    signals on the four below it.
    """
    ingest = get_settings("ingest")
    worker = get_settings("worker")
    engine = get_settings("engine")

    assert "D1" in ingest.ingest_timeframes.split(",")

    # §6.6(4) counts suspect-volume candles across every scanned timeframe, so a
    # ladder that disagrees with ingest's undercounts rather than crashing.
    assert worker.ingest_timeframes == ingest.ingest_timeframes

    assert Timeframe.D1 not in parse_signal_timeframes(engine.signal_timeframes)
