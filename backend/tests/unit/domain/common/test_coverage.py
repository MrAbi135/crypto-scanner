"""§2.12's readiness half, as a pure function shared by two callers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scanner.domain.common.coverage import Coverage, candles_behind, coverage_of
from scanner.shared import Timeframe

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def test_nothing_ever_arrived_is_not_arrivals_stopped() -> None:
    """Two states rather than one, because they call for different
    investigations: a feed that never started and a feed that died."""

    assert coverage_of(None, Timeframe.H1, NOW) is Coverage.NO_DATA
    assert coverage_of(NOW - timedelta(hours=6), Timeframe.H1, NOW) is Coverage.BEHIND


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4],
)
def test_between_closes_is_healthy_on_every_timeframe(timeframe: Timeframe) -> None:
    """The state a slow series is in for most of its life.

    An earlier probe compared the newest candle to `now` with no slack and
    reported every timeframe as broken between closes -- which is most of the
    time on H4, and made the readiness signal meaningless.
    """
    # Opened one interval ago, so it closed exactly now.
    assert coverage_of(NOW - timeframe.duration, timeframe, NOW) is Coverage.AWAITING_CLOSE


def test_the_boundary_is_one_full_interval_of_slack() -> None:
    # Closed exactly one interval ago: the next close is due now, not overdue.
    assert coverage_of(NOW - timedelta(hours=2), Timeframe.H1, NOW) is Coverage.AWAITING_CLOSE
    # A second past it.
    assert coverage_of(NOW - timedelta(hours=2, seconds=1), Timeframe.H1, NOW) is Coverage.BEHIND


def test_a_series_awaiting_a_close_is_behind_by_zero() -> None:
    """Not by one. A series between closes has missed nothing, and reporting
    "1 behind" on a healthy feed would make the number useless."""

    assert candles_behind(NOW - timedelta(hours=1), Timeframe.H1, NOW) == 0


def test_the_count_says_how_bad_rather_than_only_that_it_is_bad() -> None:
    assert candles_behind(NOW - timedelta(hours=6), Timeframe.H1, NOW) == 5
    assert candles_behind(NOW - timedelta(days=2), Timeframe.H1, NOW) == 47


def test_no_data_counts_as_zero_behind_rather_than_infinity() -> None:
    """There is no close to be behind of. The state says NO_DATA and the
    number stays out of it."""

    assert candles_behind(None, Timeframe.H1, NOW) == 0
