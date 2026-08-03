"""Continuity verifier: holes must be incident-covered or they are defects."""

from datetime import timedelta

from scanner.application.marketdata import verify_continuity
from scanner.application.ports import IncidentRecord
from scanner.shared import Timeframe, new_ulid
from tests.support.builders import BASE_TIME, make_series
from tests.unit.application.marketdata.test_backfill import FakeCandleRepo, FakeIncidentRepo

H1 = Timeframe.H1


async def test_complete_series_is_ok() -> None:
    candles = FakeCandleRepo()
    await candles.bulk_insert(make_series(48))
    report = await verify_continuity(
        candles, FakeIncidentRepo(), "BTCUSDT", H1, BASE_TIME, BASE_TIME + timedelta(hours=48)
    )
    assert report.ok and report.expected == 48 and report.present == 48


async def test_covered_hole_is_ok() -> None:
    series = make_series(48)
    candles = FakeCandleRepo()
    await candles.bulk_insert(series[:10] + series[13:])  # 3-candle hole at 10..12
    incidents = FakeIncidentRepo()
    await incidents.record(
        IncidentRecord(
            id=new_ulid(),
            scope_type="symbol_tf",
            incident_type="gap",
            started_at=series[10].open_time,  # first missing boundary (backfill contract)
            symbol="BTCUSDT",
            timeframe=H1,
            candle_span=3,
            resolution="unfillable",
        )
    )
    report = await verify_continuity(
        candles, incidents, "BTCUSDT", H1, BASE_TIME, BASE_TIME + timedelta(hours=48)
    )
    assert report.ok
    assert report.missing == 3 and report.covered_by_incidents == 3


async def test_uncovered_hole_is_a_defect_finding() -> None:
    series = make_series(48)
    candles = FakeCandleRepo()
    await candles.bulk_insert(series[:20] + series[21:])  # silent hole at 20
    report = await verify_continuity(
        candles, FakeIncidentRepo(), "BTCUSDT", H1, BASE_TIME, BASE_TIME + timedelta(hours=48)
    )
    assert not report.ok
    assert report.uncovered == [series[20].open_time]
