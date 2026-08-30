"""Warmth assessment against the SLS §1.9 gate (Sprint S3b)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scanner.application.marketdata.warmth import (
    ENGINE_TIMEFRAMES,
    ContextWarmth,
    assess_all,
    assess_context,
)
from scanner.domain.common.warmup import (
    DETECTION_MIN_CANDLES,
    VOLUME_MOMENTUM_MIN_CANDLES,
)
from scanner.shared import Timeframe

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class FakeCandleRepository:
    def __init__(self, counts: dict[tuple[str, Timeframe], int]) -> None:
        self.counts = counts
        self.queries: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def count_series(self, symbol, timeframe, start, end) -> int:
        self.queries.append((symbol, timeframe, start, end))
        return self.counts.get((symbol, timeframe), 0)


def warmth(count: int, timeframe: Timeframe = Timeframe.H1) -> ContextWarmth:
    return ContextWarmth("BTCUSDT", timeframe, count)


def test_the_gate_boundary_is_inclusive() -> None:
    """SLS §1.9 says at least 300, so 300 is warm and 299 is not.

    Pinned because an off-by-one here is invisible: the engine would simply
    decline one candle longer than the spec allows, forever, on every context.
    """
    assert warmth(DETECTION_MIN_CANDLES).detection_warm is True
    assert warmth(DETECTION_MIN_CANDLES - 1).detection_warm is False


def test_volume_warms_long_before_detection_on_a_non_seasonal_timeframe() -> None:
    partial = warmth(VOLUME_MOMENTUM_MIN_CANDLES, Timeframe.H4)

    assert partial.volume_warm is True
    assert partial.detection_warm is False


def test_seasonal_volume_needs_its_days_not_one_hundred_candles() -> None:
    """§2.11's RVOL compares against the same slot on each of the previous
    twenty days, so on H1 the volume floor is 480 candles, not 100 -- the
    flat floor reported volume-ready while RVOL still returned None (on M5
    the gap is ~19 days). G1 never lied -- it reads RVOL itself -- but this
    report did, and an operator reading it would hunt a defect that was
    arithmetic."""

    assert warmth(100, Timeframe.H1).volume_warm is False
    assert warmth(479, Timeframe.H1).volume_warm is False
    assert warmth(480, Timeframe.H1).volume_warm is True

    # M5: 20 days x 288 candles/day.
    assert warmth(5759, Timeframe.M5).volume_warm is False
    assert warmth(5760, Timeframe.M5).volume_warm is True


def test_detection_before_seasonal_volume_says_so() -> None:
    """On H1 detection (300) warms before volume (480); the report must not
    fold that window into WARM."""

    partial = warmth(DETECTION_MIN_CANDLES, Timeframe.H1)

    assert partial.detection_warm is True
    assert partial.volume_warm is False
    assert partial.describe().startswith("DETECTION_ONLY")
    assert "180 more" in partial.describe()


@pytest.mark.parametrize(
    ("count", "timeframe", "expected"),
    [
        (0, Timeframe.H1, "EMPTY"),
        (50, Timeframe.H1, "COLD"),
        # H1 is seasonal: at 150 candles volume (480) is as unmet as
        # detection (300), so VOLUME_ONLY belongs to a non-seasonal rung.
        (150, Timeframe.H1, "COLD"),
        (150, Timeframe.H4, "VOLUME_ONLY"),
        (DETECTION_MIN_CANDLES, Timeframe.H1, "DETECTION_ONLY"),
        (480, Timeframe.H1, "WARM"),
        (DETECTION_MIN_CANDLES, Timeframe.H4, "WARM"),
    ],
)
def test_each_state_describes_itself(count: int, timeframe: Timeframe, expected: str) -> None:
    assert warmth(count, timeframe).describe().startswith(expected)


def test_a_cold_context_reports_how_far_it_has_to_go() -> None:
    """The number an operator actually needs: how much more history to fetch."""
    assert warmth(250).candles_short == 50
    assert "50 more" in warmth(250).describe()


def test_a_warm_context_is_not_short() -> None:
    assert warmth(DETECTION_MIN_CANDLES + 1000).candles_short == 0


@pytest.mark.asyncio
async def test_the_count_is_bounded_at_now_not_at_the_newest_row() -> None:
    """Staleness is FreshnessTracker's question, not this module's.

    A series that stopped updating a month ago still holds whatever history it
    holds. Reporting it as cold would hide a stale-feed problem behind a
    warm-up one, and the two need different fixes.
    """
    repo = FakeCandleRepository({("BTCUSDT", Timeframe.H1): 400})

    report = await assess_context(repo, "BTCUSDT", Timeframe.H1, now=NOW)

    assert report.closed_candles == 400
    assert report.detection_warm is True

    _, _, _, end = repo.queries[0]

    assert end == NOW


@pytest.mark.asyncio
async def test_every_requested_context_is_reported_even_when_empty() -> None:
    """An unreported context reads as absent; an EMPTY one reads as a problem."""
    repo = FakeCandleRepository({("BTCUSDT", Timeframe.H1): 500})

    reports = await assess_all(
        repo,
        (
            ("BTCUSDT", Timeframe.H1),
            ("BTCUSDT", Timeframe.H4),
            ("ETHUSDT", Timeframe.H1),
        ),
        now=NOW,
    )

    assert len(reports) == 3
    assert [report.detection_warm for report in reports] == [True, False, False]
    assert reports[1].describe() == "EMPTY"


def test_the_scanned_timeframe_set_is_the_enum_itself() -> None:
    """A second list would be a place for the two to disagree (SLS §0.2)."""
    assert tuple(Timeframe) == ENGINE_TIMEFRAMES
    assert Timeframe.M5 in ENGINE_TIMEFRAMES
