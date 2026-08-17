"""Live candle ingest orchestration (Sprint S2)."""

from __future__ import annotations

from datetime import datetime

from scanner.application.marketdata.backfill import BackfillService
from scanner.application.marketdata.freshness import (
    FreshnessState,
    FreshnessTracker,
)
from scanner.application.marketdata.validation import validate_batch
from scanner.application.ports import CandleRepository, Clock
from scanner.domain.common import Candle
from scanner.shared import Timeframe


class LiveIngestService:
    """Validate, repair gaps, track freshness, and persist stream candles."""

    def __init__(
        self,
        candles: CandleRepository,
        backfill: BackfillService,
        clock: Clock,
    ) -> None:
        self._candles = candles
        self._backfill = backfill
        self._clock = clock
        self._trackers: dict[tuple[str, Timeframe], FreshnessTracker] = {}

    def freshness(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> FreshnessState:
        """Return the current freshness state for one series."""
        return self._tracker(symbol, timeframe).state

    def detection_allowed(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> bool:
        """Whether downstream detection may consume this series."""
        return self._tracker(symbol, timeframe).detection_allowed

    def has_observation(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> bool:
        """Whether at least one exchange event has been observed."""
        return self._tracker(symbol, timeframe).last_event_at is not None

    async def ingest(
        self,
        candle: Candle,
        event_at: datetime,
    ) -> int:
        """Process one closed live candle."""

        tracker = self._tracker(
            candle.symbol,
            candle.timeframe,
        )

        tracker.observe_event(
            event_at=event_at,
            observed_at=self._clock.now(),
        )

        tail = await self._candles.latest_open_time(
            candle.symbol,
            candle.timeframe,
        )

        # Duplicate or old stream frame: persistence remains append-only.
        if tail is not None and candle.open_time <= tail:
            return 0

        # Recover missing closed candles before accepting the live candle.
        if tail is not None:
            expected_open = tail + candle.timeframe.duration

            if candle.open_time > expected_open:
                report = await self._backfill.backfill(
                    candle.symbol,
                    candle.timeframe,
                    expected_open,
                    candle.open_time,
                )

                if report.gaps_recorded > 0:
                    tracker.mark_degraded()
                    tracker.break_recovery()

                if report.quarantined_batches > 0:
                    tracker.mark_suspect()
                    tracker.break_recovery()

                tail = await self._candles.latest_open_time(
                    candle.symbol,
                    candle.timeframe,
                )

        result = validate_batch(
            [candle],
            expected_prev_open=tail,
        )

        if not result.ok:
            tracker.mark_suspect()
            return 0

        if result.gaps:
            tracker.mark_degraded()
            return 0

        # The one place a close is announced. Backfill inserts through the same
        # repository without this flag -- see CandleRepository.bulk_insert.
        inserted = await self._candles.bulk_insert(
            [candle],
            emit_outbox=True,
        )

        if inserted > 0:
            tracker.record_verified_candle()

        return inserted

    def _tracker(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> FreshnessTracker:
        key = (symbol, timeframe)

        tracker = self._trackers.get(key)

        if tracker is None:
            tracker = FreshnessTracker(
                symbol=symbol,
                timeframe=timeframe,
            )
            self._trackers[key] = tracker

        return tracker
