"""Backfill orchestration (Roadmap S1): chunked, validated, idempotent.

Guarantees:
- Idempotent: re-running a completed range inserts nothing (resume from the
  persisted tail; the repository's conflict-skip is the second line).
- Validated-before-persisted: a batch that fails fatal validation is
  refetched once; if still corrupt it is quarantined (incident, nothing
  persisted) — corrupt data never reaches the record (SLS §2.15).
- Honest: every gap the venue itself carries (delistings, outages) is
  recorded in the incident ledger with its exact span (DDD T8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from scanner.application.marketdata.validation import ValidationFinding, validate_batch
from scanner.application.ports import (
    CandleRepository,
    Clock,
    IncidentRecord,
    IncidentRepository,
    MarketDataProvider,
)
from scanner.shared import Timeframe, ValidationError, new_ulid
from scanner.shared.timeutil import floor_to_boundary

_CHUNK = 1000  # Binance klines max per request; also our persistence batch unit


@dataclass(slots=True)
class BackfillReport:
    symbol: str
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    fetched: int = 0
    inserted: int = 0
    gaps_recorded: int = 0
    quarantined_batches: int = 0
    resumed_from: datetime | None = None
    findings: list[str] = field(default_factory=list)


class BackfillService:
    def __init__(
        self,
        provider: MarketDataProvider,
        candles: CandleRepository,
        incidents: IncidentRepository,
        clock: Clock,
    ) -> None:
        self._provider = provider
        self._candles = candles
        self._incidents = incidents
        self._clock = clock

    async def backfill(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime | None = None,
    ) -> BackfillReport:
        """Fill [start, end) for one series. end defaults to the last closed
        boundary — the forming candle is never fetched (SLS §0.1).
        """
        now = self._clock.now()
        effective_end = floor_to_boundary(end or now, timeframe)
        aligned_start = floor_to_boundary(start, timeframe)
        if aligned_start >= effective_end:
            raise ValidationError(
                f"empty backfill range for {symbol} {timeframe.value}: "
                f"{aligned_start.isoformat()} >= {effective_end.isoformat()}"
            )

        report = BackfillReport(symbol, timeframe, aligned_start, effective_end)

        # Idempotent resume: never refetch what the record already holds.
        tail = await self._candles.latest_open_time(symbol, timeframe)
        cursor = aligned_start
        prev_open: datetime | None = None
        if tail is not None and tail >= aligned_start:
            cursor = tail + timeframe.duration
            prev_open = tail
            report.resumed_from = cursor
            if cursor >= effective_end:
                return report  # fully covered — a re-run is a no-op

        while cursor < effective_end:
            chunk_end = min(cursor + timeframe.duration * _CHUNK, effective_end)
            batch = list(
                await self._provider.fetch_candles(
                    symbol, timeframe, cursor, chunk_end, limit=_CHUNK
                )
            )
            report.fetched += len(batch)

            if not batch:
                # The venue has nothing in this window (pre-listing span or
                # exchange outage). Record the honest hole and move on.
                missing = int((chunk_end - cursor) / timeframe.duration)
                await self._record_gap(
                    report, symbol, timeframe, cursor, missing, "empty window from venue"
                )
                cursor = chunk_end
                prev_open = None  # continuity restarts after a recorded hole
                continue

            result = validate_batch(batch, expected_prev_open=prev_open)
            if not result.ok:
                # One refetch: transient corruption should not quarantine a span.
                batch = list(
                    await self._provider.fetch_candles(
                        symbol, timeframe, cursor, chunk_end, limit=_CHUNK
                    )
                )
                result = validate_batch(batch, expected_prev_open=prev_open)
                if not result.ok:
                    await self._quarantine(report, symbol, timeframe, cursor, result.findings)
                    cursor = chunk_end
                    prev_open = None
                    continue

            for gap in result.gaps:
                # gap.open_time = last present candle before the hole; the
                # incident's started_at is the FIRST MISSING boundary (DDD T8).
                first_missing = (gap.open_time or cursor) + timeframe.duration
                await self._record_gap(
                    report, symbol, timeframe, first_missing, gap.gap_candles, gap.message
                )

            report.inserted += await self._candles.bulk_insert(batch)
            prev_open = batch[-1].open_time
            cursor = prev_open + timeframe.duration

        return report

    async def _record_gap(
        self,
        report: BackfillReport,
        symbol: str,
        timeframe: Timeframe,
        at: datetime,  # first missing boundary; span counts consecutive missing candles
        span: int,
        notes: str,
    ) -> None:
        now = self._clock.now()
        await self._incidents.record(
            IncidentRecord(
                id=new_ulid(),
                scope_type="symbol_tf",
                incident_type="gap",
                started_at=at,
                symbol=symbol,
                timeframe=timeframe,
                candle_span=span,
                resolution="unfillable",  # batch mode: the venue itself lacks the data
                resolved_at=now,
                notes=notes,
            )
        )
        report.gaps_recorded += 1
        report.findings.append(notes)

    async def _quarantine(
        self,
        report: BackfillReport,
        symbol: str,
        timeframe: Timeframe,
        at: datetime,
        findings: tuple[ValidationFinding, ...],
    ) -> None:
        await self._incidents.record(
            IncidentRecord(
                id=new_ulid(),
                scope_type="symbol_tf",
                incident_type="validation_failure",
                started_at=at,
                symbol=symbol,
                timeframe=timeframe,
                candle_span=_CHUNK,
                resolution=None,  # open: needs operator attention (runbook: backfill)
                notes="; ".join(f.message for f in findings[:5]),
            )
        )
        report.quarantined_batches += 1
        report.findings.extend(f.message for f in findings[:5])
