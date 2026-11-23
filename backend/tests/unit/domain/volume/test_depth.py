"""§6.4's depth input (SLS v1.0.20): which daily snapshot a candle may read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.domain.volume.depth import (
    DEPTH_MEDIAN_SNAPSHOTS,
    DepthSnapshot,
    depth_at,
    latest_readable_stamp,
)

DAY = timedelta(days=1)
D0 = datetime(2026, 10, 1, tzinfo=UTC)


def days(depths: list[str], start: datetime = D0) -> list[DepthSnapshot]:
    return [DepthSnapshot(start + i * DAY, Decimal(d)) for i, d in enumerate(depths)]


def test_a_snapshot_is_readable_only_from_the_end_of_its_day() -> None:
    # Owner ruling M2 Q1 (a): the book behind day D's stamp is read later on
    # day D, so only a candle closing at or after D+1 00:00 may read it.
    stamp = D0 + 9 * DAY
    assert latest_readable_stamp(stamp + DAY) == stamp
    assert latest_readable_stamp(stamp + DAY - timedelta(milliseconds=1)) < stamp


def test_the_candle_reads_the_latest_readable_snapshot_and_its_seven_day_median() -> None:
    snaps = days(["10", "20", "30", "40", "50", "60", "70", "80"])
    close = snaps[-1].stamped_at + DAY

    reading = depth_at(snaps, close)

    assert reading is not None
    assert reading.depth == Decimal("80")
    assert reading.stamped_at == snaps[-1].stamped_at
    # the seven ending at it: 20..80, median 50; the oldest (10) is not in it
    assert reading.median_depth_7d == Decimal("50")


def test_a_snapshot_stamped_on_the_closing_day_is_never_read() -> None:
    snaps = days(["10", "20", "30", "40", "50", "60", "70", "1"])
    # one millisecond before the last snapshot's day ends: it is not readable
    close = snaps[-1].stamped_at + DAY - timedelta(milliseconds=1)

    reading = depth_at(snaps, close)

    assert reading is not None
    assert reading.stamped_at == snaps[-2].stamped_at
    assert reading.depth == Decimal("70")


def test_a_later_snapshot_passed_in_by_mistake_cannot_leak() -> None:
    snaps = days(["10", "20", "30", "40", "50", "60", "70"])
    close = snaps[-1].stamped_at + DAY
    future = DepthSnapshot(close, Decimal("999999"))

    assert depth_at([*snaps, future], close) == depth_at(snaps, close)


def test_order_of_the_input_does_not_matter() -> None:
    snaps = days(["10", "20", "30", "40", "50", "60", "70"])
    close = snaps[-1].stamped_at + DAY

    assert depth_at(list(reversed(snaps)), close) == depth_at(snaps, close)


def test_fewer_than_seven_readable_snapshots_is_out_of_reach() -> None:
    # Owner ruling M2 Q2 (a): no median of fewer, the check stays incomplete.
    snaps = days(["10"] * (DEPTH_MEDIAN_SNAPSHOTS - 1))
    close = snaps[-1].stamped_at + DAY

    assert depth_at(snaps, close) is None
    assert depth_at([], close) is None


def test_exactly_seven_readable_snapshots_is_enough() -> None:
    snaps = days(["10"] * DEPTH_MEDIAN_SNAPSHOTS)
    close = snaps[-1].stamped_at + DAY

    reading = depth_at(snaps, close)

    assert reading is not None
    assert reading.median_depth_7d == Decimal("10")
