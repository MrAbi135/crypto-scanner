"""Backfill orchestrator: idempotency, gap incidents, quarantine — via fakes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from scanner.application.marketdata import BackfillService
from scanner.application.ports import IncidentRecord
from scanner.domain.common import Candle
from scanner.shared import Timeframe, ValidationError
from tests.support.builders import BASE_TIME, make_series
from tests.support.clock import FakeClock


class FakeProvider:
    def __init__(self, series: list[Candle]) -> None:
        self._series = {c.open_time: c for c in series}
        self.calls = 0

    async def fetch_symbols(self):  # pragma: no cover - unused here
        return []

    async def fetch_candles(self, symbol, timeframe, start, end, *, limit) -> Sequence[Candle]:
        self.calls += 1
        out = [c for t, c in sorted(self._series.items()) if start <= t < end]
        return out[:limit]


class FakeCandleRepo:
    def __init__(self) -> None:
        self.rows: dict[datetime, Candle] = {}

    async def bulk_insert(self, candles: Sequence[Candle]) -> int:
        inserted = 0
        for c in candles:
            if c.open_time not in self.rows:
                self.rows[c.open_time] = c
                inserted += 1
        return inserted

    async def latest_open_time(self, symbol, timeframe):
        return max(self.rows) if self.rows else None

    async def fetch_series(self, symbol, timeframe, start, end):
        return [c for t, c in sorted(self.rows.items()) if start <= t < end]

    async def count_series(self, symbol, timeframe, start, end) -> int:
        return len([t for t in self.rows if start <= t < end])


class FakeIncidentRepo:
    def __init__(self) -> None:
        self.records: list[IncidentRecord] = []

    async def record(self, incident: IncidentRecord) -> None:
        self.records.append(incident)

    async def resolve(self, incident_id, *, resolution, resolved_at):  # pragma: no cover
        raise NotImplementedError

    async def list_open(self, symbol=None):
        return [r for r in self.records if r.resolved_at is None]

    async def list_for_series(self, symbol, timeframe):
        return [r for r in self.records if r.symbol == symbol and r.timeframe is timeframe]


def _service(series: list[Candle], clock_at: datetime):
    provider = FakeProvider(series)
    candles = FakeCandleRepo()
    incidents = FakeIncidentRepo()
    service = BackfillService(provider, candles, incidents, FakeClock(clock_at))
    return service, provider, candles, incidents


H1 = Timeframe.H1
END = BASE_TIME + timedelta(hours=100)


async def test_full_backfill_inserts_everything() -> None:
    series = make_series(100)
    service, _, candles, incidents = _service(series, END)
    report = await service.backfill("BTCUSDT", H1, BASE_TIME, END)
    assert report.inserted == 100 and report.fetched == 100
    assert not incidents.records
    assert len(candles.rows) == 100


async def test_rerun_is_a_noop(  # idempotency: Roadmap S1 DoD
) -> None:
    series = make_series(100)
    service, provider, candles, _ = _service(series, END)
    await service.backfill("BTCUSDT", H1, BASE_TIME, END)
    calls_before = provider.calls
    report2 = await service.backfill("BTCUSDT", H1, BASE_TIME, END)
    assert report2.inserted == 0 and report2.fetched == 0
    assert provider.calls == calls_before  # resume logic short-circuits, no refetch
    assert len(candles.rows) == 100


async def test_resume_fills_only_the_missing_tail() -> None:
    series = make_series(100)
    service, _, candles, _ = _service(series, END)
    await service.backfill("BTCUSDT", H1, BASE_TIME, BASE_TIME + timedelta(hours=60))
    report = await service.backfill("BTCUSDT", H1, BASE_TIME, END)
    assert report.resumed_from == BASE_TIME + timedelta(hours=60)
    assert report.inserted == 40
    assert len(candles.rows) == 100


async def test_venue_gap_is_recorded_as_incident() -> None:
    series = make_series(100)
    holed = series[:40] + series[43:]  # venue lacks 3 candles
    service, _, _candles, incidents = _service(holed, END)
    report = await service.backfill("BTCUSDT", H1, BASE_TIME, END)
    assert report.inserted == 97
    assert report.gaps_recorded == 1
    inc = incidents.records[0]
    assert inc.incident_type == "gap" and inc.candle_span == 3
    assert inc.started_at == series[40].open_time  # first missing boundary
    assert inc.resolution == "unfillable"


async def test_forming_candle_is_never_fetched() -> None:
    series = make_series(100)
    service, _, candles, _ = _service(series, BASE_TIME + timedelta(hours=99, minutes=30))
    report = await service.backfill("BTCUSDT", H1, BASE_TIME, None)
    # now is mid-candle 99 ⇒ effective end floors to hour 99: candles 0..98 only
    assert report.inserted == 99
    assert max(candles.rows) == BASE_TIME + timedelta(hours=98)


async def test_empty_range_rejected() -> None:
    service, _, _, _ = _service([], END)
    with pytest.raises(ValidationError, match="empty backfill range"):
        await service.backfill("BTCUSDT", H1, END, END)


async def test_corrupt_batch_quarantined_after_one_refetch() -> None:
    series = make_series(10)
    corrupted = [*series[:3], series[2], *series[3:]]  # duplicate ⇒ fatal validation

    class CorruptProvider(FakeProvider):
        async def fetch_candles(self, symbol, timeframe, start, end, *, limit):
            self.calls += 1
            return [c for c in corrupted if start <= c.open_time < end][:limit]

    provider = CorruptProvider([])
    candles = FakeCandleRepo()
    incidents = FakeIncidentRepo()
    service = BackfillService(provider, candles, incidents, FakeClock(END))
    report = await service.backfill("BTCUSDT", H1, BASE_TIME, BASE_TIME + timedelta(hours=10))
    assert report.quarantined_batches == 1
    assert not candles.rows  # nothing corrupt was persisted
    assert provider.calls == 2  # original + one refetch
    assert incidents.records[0].incident_type == "validation_failure"
    assert incidents.records[0].resolution is None  # open — operator attention
