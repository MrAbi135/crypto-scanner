"""§6.4's depth input: which daily depth snapshot a candle may read.

SLS v1.0.20 §6.4, "The depth input": the depth for test (2) is the latest
daily ±2% depth snapshot taken at or before the candle's close, and the 7-day
median is taken over the seven daily snapshots ending at that one. A snapshot
taken after the candle's close is never read.

**When a snapshot was taken.** The daily collector stamps a row with the start
of the UTC day it runs on, and reads the order book later that day, when the
job actually runs. The stamp is therefore not the moment the book was read:
a candle closing at 06:00 on day D could otherwise read a book taken at 09:00
on day D. The owner's ruling (M2 Q1, option a) closes that: a snapshot stamped
day D counts as taken at the end of day D, so only a candle closing at or after
D+1 00:00 UTC may read it. That holds as long as the job finishes within its
own day, and needs no extra column.

**Fewer than seven snapshots** (a newly listed symbol): the depth is out of
reach and the reading is None, so the check is incomplete rather than failed
(owner, M2 Q2, option a).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from scanner.domain.common import median

DEPTH_MEDIAN_SNAPSHOTS = 7

# A snapshot stamped with day D's start is treated as taken at D's end.
SNAPSHOT_TAKEN_BY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    """One daily ±2% depth row: the day stamp it carries, and its depth."""

    stamped_at: datetime
    depth: Decimal


@dataclass(frozen=True, slots=True)
class DepthReading:
    """The two inputs of §6.4's test (2) for one candle."""

    depth: Decimal
    median_depth_7d: Decimal
    # The day stamp of the snapshot read, kept so a reader can show which.
    stamped_at: datetime


def latest_readable_stamp(close_time: datetime) -> datetime:
    """The newest day stamp a candle closing at `close_time` may read.

    A stamp of day D is readable from D + SNAPSHOT_TAKEN_BY on, so the newest
    readable stamp is the close minus that interval (inclusive).
    """
    return close_time - SNAPSHOT_TAKEN_BY


def depth_at(snapshots: Sequence[DepthSnapshot], close_time: datetime) -> DepthReading | None:
    """§6.4's depth and 7-day median for a candle closing at `close_time`.

    `snapshots` may arrive in any order and may include snapshots the candle
    must not read; those are dropped here, so a caller that over-fetches can
    never leak a later book into the verdict.
    """
    cutoff = latest_readable_stamp(close_time)

    readable = sorted(
        (s for s in snapshots if s.stamped_at <= cutoff),
        key=lambda s: s.stamped_at,
    )

    if len(readable) < DEPTH_MEDIAN_SNAPSHOTS:
        return None

    window = readable[-DEPTH_MEDIAN_SNAPSHOTS:]
    middle = median([s.depth for s in window])

    if middle is None:
        return None

    return DepthReading(
        depth=window[-1].depth,
        median_depth_7d=middle,
        stamped_at=window[-1].stamped_at,
    )
