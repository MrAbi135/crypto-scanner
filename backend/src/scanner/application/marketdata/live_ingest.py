"""Live candle ingest orchestration (Sprint S2)."""

from __future__ import annotations

from scanner.application.marketdata.backfill import BackfillService
from scanner.application.marketdata.validation import validate_batch
from scanner.application.ports import CandleRepository
from scanner.domain.common import Candle


class LiveIngestService:
    """Validate, repair gaps, and persist closed stream candles."""

    def __init__(
        self,
        candles: CandleRepository,
        backfill: BackfillService,
    ) -> None:
        self._candles = candles
        self._backfill = backfill

    async def ingest(self, candle: Candle) -> int:
        """Persist one closed stream candle after continuity verification."""

        tail = await self._candles.latest_open_time(
            candle.symbol,
            candle.timeframe,
        )

        # Duplicate or stale stream frame: persistence is append-only.
        if tail is not None and candle.open_time <= tail:
            return 0

        # Recover any missing closed candles before accepting the live candle.
        if tail is not None:
            expected_open = tail + candle.timeframe.duration

            if candle.open_time > expected_open:
                await self._backfill.backfill(
                    candle.symbol,
                    candle.timeframe,
                    expected_open,
                    candle.open_time,
                )

                tail = await self._candles.latest_open_time(
                    candle.symbol,
                    candle.timeframe,
                )

        result = validate_batch(
            [candle],
            expected_prev_open=tail,
        )

        if not result.ok or result.gaps:
            return 0

        return await self._candles.bulk_insert([candle])
