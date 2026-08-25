"""Read port for §18.8's collection rows — the published archive.

Separate from `SignalRepository` deliberately. That port is the crown jewel's
write side: append, read one back, and nothing that could be mistaken for a
mutation. These are reporting queries over T17 joined to T19, and hanging them
off the same protocol would mean every fake in the test suite grows a filtered
keyset query it does not use.

**PRD FC-10.1 draws a line this port has to carry:** "Delisting-expired signals
excluded from quality stats but present in archive". The archive and the
statistics are two different populations over the same rows, so the exclusion
lives on the statistics read and not on the history one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from scanner.application.ports.signals import SignalRecord


@dataclass(frozen=True, slots=True)
class ArchivedSignal:
    """One published signal and its outcome, if it has resolved yet.

    `outcome` is None for a signal still live. Modelled as an absent object
    rather than a row of zeroes, because a zero MFE is a real measurement and
    "not measured yet" is not.
    """

    signal: SignalRecord
    outcome: str | None = None
    resolved_at: datetime | None = None
    elapsed_candles: int | None = None
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    excluded_from_stats: bool = False


@dataclass(frozen=True, slots=True)
class HistoryFilters:
    """§18.8's history filters: "outcome, archetype, grade, tf, symbol_id,
    version, date range".

    Every field is a tuple or a bound, and empty means "no constraint". The
    endpoint has already refused anything outside the closed set — §9's rule
    that an unapplied filter is a lie is enforced at the grammar, so by the
    time a value reaches here it is one this port is expected to apply.
    """

    outcomes: tuple[str, ...] = ()
    archetypes: tuple[str, ...] = ()
    grades: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    algo_versions: tuple[str, ...] = ()
    published_from: datetime | None = None
    published_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class HistoryPage:
    rows: tuple[ArchivedSignal, ...]
    # The position to resume from, or None at the end. Built by the repository
    # because only it knows the sort key it actually applied.
    next_position: dict[str, str] | None


class TrackRecordRepository(Protocol):
    async def history(
        self,
        filters: HistoryFilters,
        *,
        limit: int,
        after: dict[str, str] | None = None,
    ) -> HistoryPage:
        """§18.8's archive, newest first, keyset paginated.

        `after` is the decoded cursor position. §8 requires that "a paginated
        walk never skips or duplicates under concurrent inserts", which is why
        the position is the full sort key rather than a timestamp: signals
        published on the same close would otherwise straddle a page boundary
        and one of them would be lost.
        """
        ...


class GroupBy(str, Enum):
    """§18.8's `group_by`: "archetype/grade/tf/version"."""

    ARCHETYPE = "archetype"
    GRADE = "grade"
    TIMEFRAME = "timeframe"
    # Grouping *by* version as well, which is not the same as segmenting by it.
    # Segmentation always happens; this asks for version to be the only axis.
    VERSION = "version"


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    """One group's terminal counts, straight from the database.

    Split by outcome rather than pre-aggregated into a rate, so the arithmetic
    that decides what is rated and what is merely reported lives in
    `domain/lifecycle/track_record.py` where it is testable without a
    database — and so a new terminal state does not silently vanish into a
    denominator.
    """

    algo_version: str
    # The value of whatever axis was grouped on, or None when the axis *is*
    # the version.
    key: str | None
    successes: int
    failures: int
    expired: int
    invalidated: int


class TrackRecordStatistics(Protocol):
    async def outcome_counts(
        self,
        *,
        group_by: GroupBy,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[OutcomeCounts, ...]:
        """§18.8's aggregate: "per-group n, success/failed/expired".

        **Version-segmented always** (§18.8's own note). Every returned group
        carries its `algo_version` whatever axis was requested, because a hit
        rate that mixes algorithm versions is the average of two different
        scanners — and the number it produces describes neither.

        **Excludes rows flagged `excluded_from_stats`** — PRD FC-10.1's
        "Delisting-expired signals excluded from quality stats but present in
        archive". The archive read does not apply this; the two are different
        populations over the same table and that is the line between them.
        """
        ...
