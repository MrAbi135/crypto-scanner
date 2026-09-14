"""§6.6(4)'s suspect-volume count reads the running participation generation only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scanner.application.marketdata.fake_volume_job import SuspectVolumeCounter
from scanner.application.ports.detection import EngineEventRecord
from scanner.shared import Timeframe

AT = datetime(2026, 9, 1, tzinfo=UTC)


def suspect(key: str, version: str) -> EngineEventRecord:
    return EngineEventRecord(
        event_key=key,
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        event_type="VOLUME_SUSPECT",
        event_at=AT,
        algo_version=version,
        payload="{}",
        created_at=AT,
    )


class Events:
    """One suspect candle recorded by two participation generations."""

    async def list_events(self, symbol, timeframe, start, end, *, only_versions=None):
        return tuple(
            record
            for record in (suspect("old", "s7-old"), suspect("new", "s7-new"))
            if only_versions is None or record.algo_version in only_versions
        )


@pytest.mark.asyncio
async def test_a_suspect_candle_is_counted_once_across_a_version_bump() -> None:
    """Audit class C: a participation bump re-derives a day of VOLUME_SUSPECT
    under the new label while the old rows stay, and §6.6 compares the count
    against five -- both generations would double it."""
    end = AT + timedelta(days=1)

    unpinned = SuspectVolumeCounter(Events(), (Timeframe.H1,))
    pinned = SuspectVolumeCounter(Events(), (Timeframe.H1,), only_versions=frozenset({"s7-new"}))

    assert await unpinned.count("BTCUSDT", AT, end) == 2
    assert await pinned.count("BTCUSDT", AT, end) == 1
