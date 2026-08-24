"""Turn one candle close into a detection window (Sprint S4b).

The replay services take a window, not a candle. So a live close is served by
replaying the trailing window that ends at it. Re-processing the window on every
close is more work than an incremental update would be, and it is deliberate:

* every detection write is already idempotent through persistence uniqueness,
  so a re-run converges rather than duplicating;
* the services are the ones the golden suite verifies, so live output and
  golden output come from the same code rather than from two implementations
  that must be kept in agreement.

An incremental engine is the eventual optimisation. It is not the thing to
build on the day the pipeline first runs unattended.

**The window must exceed the warm-up gate.** SLS §1.9 needs 300 closed candles
before structure, liquidity or ICT detection is allowed. A window of 300 leaves
the newest close sitting exactly on the boundary; anything below it and the
engine runs forever, finds itself cold every time, and emits nothing at all --
while looking perfectly healthy.
"""

from __future__ import annotations

import time
from datetime import datetime

import structlog

from scanner.application.detection.pipeline import DetectionPipeline
from scanner.application.ports.metrics import DetectionMetrics, NullMetrics
from scanner.domain.common.warmup import DETECTION_MIN_CANDLES
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)

DEFAULT_WINDOW_CANDLES = 500


class TrailingWindowRunner:
    """Replay the window ending at a freshly closed candle."""

    def __init__(
        self,
        pipeline: DetectionPipeline,
        *,
        window_candles: int = DEFAULT_WINDOW_CANDLES,
        metrics: DetectionMetrics | None = None,
    ) -> None:
        if window_candles <= DETECTION_MIN_CANDLES:
            raise ValueError(
                f"window_candles must exceed the SLS §1.9 warm-up minimum of "
                f"{DETECTION_MIN_CANDLES}; got {window_candles}"
            )

        self._pipeline = pipeline
        self._window = window_candles
        self._metrics = metrics or NullMetrics()

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        open_time: datetime,
    ) -> None:
        step = timeframe.duration

        # `end` is exclusive in fetch_series, so it must clear the closing
        # candle's own open_time or the close that triggered this pass is the
        # one candle the pass cannot see.
        end = open_time + step
        start = open_time - step * self._window

        # SLS §14: "Candle close -> all detectors evaluated (per symbol-TF)
        # <= 2 s". Timed around the pipeline and nothing else, so the number
        # is the work rather than the consumer's dispatch overhead.
        #
        # `perf_counter` rather than the injected clock: this is a duration,
        # and a wall clock that steps -- NTP, a leap second, a VM resuming --
        # produces a negative or absurd one. The clock exists for timestamps
        # that have to agree with stored data; this one does not.
        started = time.perf_counter()

        report = await self._pipeline.run(symbol, timeframe, start, end)

        elapsed = time.perf_counter() - started

        self._metrics.observe_pass(elapsed, symbol=symbol, timeframe=timeframe.value)

        log.info(
            "detection_pass_completed",
            symbol=symbol,
            timeframe=timeframe.value,
            open_time=open_time.isoformat(),
            window_candles=self._window,
            candles=report.structure.candles,
            events_inserted=report.structure.events_inserted,
            trend=report.structure_shift.trend_state,
            pools_upserted=report.liquidity.pools_upserted,
            sweeps=report.liquidity.sweeps,
            elapsed_seconds=round(elapsed, 3),
        )
