"""Bring cold contexts up to the detection gate at boot (Sprint S3b).

Without this a fresh deployment is useless for days: the engine consumes every
close, the SLS §1.9 gate declines all of them for want of 300 candles, and
nothing anywhere says so. M5 reaches the gate in about a day; H4 takes fifty.

So on boot each configured context is topped up from the REST history to the
configured depth. Backfill inserts without `emit_outbox`, so this warms the
record without replaying hundreds of historical closes through the live
detection path -- see `CandleRepository.bulk_insert`.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from scanner.application.marketdata.backfill import BackfillService
from scanner.application.marketdata.warmth import assess_context
from scanner.application.ports import CandleRepository, Clock
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WarmupResult:
    symbol: str
    timeframe: Timeframe
    candles_before: int
    candles_after: int
    skipped: bool


class WarmupBackfillService:
    """Top every configured context up to the detection gate."""

    def __init__(
        self,
        candles: CandleRepository,
        backfill: BackfillService,
        clock: Clock,
        *,
        target_candles: int,
    ) -> None:
        self._candles = candles
        self._backfill = backfill
        self._clock = clock
        self._target = target_candles

    async def warm(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> WarmupResult:
        now = self._clock.now()

        before = await assess_context(self._candles, symbol, timeframe, now=now)

        if before.closed_candles >= self._target:
            return WarmupResult(
                symbol=symbol,
                timeframe=timeframe,
                candles_before=before.closed_candles,
                candles_after=before.closed_candles,
                skipped=True,
            )

        # Always fetch the full target rather than only the shortfall. The
        # existing rows may be an old island with a hole in front of them, and
        # ON CONFLICT DO NOTHING makes re-fetching what we already hold cheap
        # and harmless. Asking only for the difference would leave the hole.
        start = now - timeframe.duration * self._target

        await self._backfill.backfill(symbol, timeframe, start, now)

        after = await assess_context(self._candles, symbol, timeframe, now=now)

        log.info(
            "context_warmed",
            symbol=symbol,
            timeframe=timeframe.value,
            candles_before=before.closed_candles,
            candles_after=after.closed_candles,
            detection_warm=after.detection_warm,
        )

        return WarmupResult(
            symbol=symbol,
            timeframe=timeframe,
            candles_before=before.closed_candles,
            candles_after=after.closed_candles,
            skipped=False,
        )

    async def warm_all(
        self,
        symbols: tuple[str, ...],
        timeframes: tuple[Timeframe, ...],
    ) -> tuple[WarmupResult, ...]:
        """Warm every context, lowest timeframe first.

        Bottom-up because a higher timeframe reads the one below it for zone
        confirmation; warming H1 before M15 would leave a window where H1 runs
        against an empty LTF and confirms nothing.

        One context failing does not stop the rest: a delisted or thin symbol
        must not leave the whole universe cold.
        """
        results: list[WarmupResult] = []

        for timeframe in timeframes:
            for symbol in symbols:
                try:
                    results.append(await self.warm(symbol, timeframe))
                except Exception:
                    log.exception(
                        "context_warmup_failed",
                        symbol=symbol,
                        timeframe=timeframe.value,
                    )

        return tuple(results)
