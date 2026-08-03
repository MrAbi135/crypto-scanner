"""Continuity verification over the persisted record (CLI: verify-continuity).

Reads the stored series and proves it hole-free against the incident
ledger: every missing candle must be covered by a recorded gap incident —
an uncovered hole is a defect finding (SLS §2.15 applied to our own record).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from scanner.application.ports import CandleRepository, IncidentRepository
from scanner.shared import Timeframe
from scanner.shared.timeutil import span_boundaries


@dataclass(slots=True)
class ContinuityReport:
    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    expected: int = 0
    present: int = 0
    missing: int = 0
    covered_by_incidents: int = 0
    uncovered: list[datetime] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.uncovered


async def verify_continuity(
    candles: CandleRepository,
    incidents: IncidentRepository,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> ContinuityReport:
    report = ContinuityReport(symbol, timeframe, start, end)
    series = await candles.fetch_series(symbol, timeframe, start, end)
    present = {c.open_time for c in series}
    all_incidents = await incidents.list_for_series(symbol, timeframe)

    covered: set[datetime] = set()
    for inc in all_incidents:
        if inc.incident_type != "gap":
            continue
        # started_at = first missing boundary (backfill contract)
        for i in range(inc.candle_span):
            covered.add(inc.started_at + timeframe.duration * i)

    for boundary in span_boundaries(start, end, timeframe):
        report.expected += 1
        if boundary in present:
            report.present += 1
            continue
        report.missing += 1
        if boundary in covered:
            report.covered_by_incidents += 1
        else:
            report.uncovered.append(boundary)
    return report
