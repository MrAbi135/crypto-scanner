"""Run the volume and momentum engines over a window (SLS §6, §7).

The two travel together because they answer the same question from different
sides — who is participating, and with how much energy — and because §7.1's
participation component reads RVOL, so computing them separately would walk the
same window twice.

**What this records is deliberately narrow.** RVOL class is a per-candle
measurement on every bar, and writing 500 rows per replay would bury the
detection log in noise nobody reads. Only the events §6 and §7 call *facts*
are persisted: spikes, expansion and contraction flags, and momentum readings
at phase changes. The continuous series stays computable on demand from the
candles, which is where it already lives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from scanner.application.detection.orchestrator import build_event_key
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.detection import (
    EngineEventRecord,
    EngineEventRepository,
)
from scanner.domain.common import Candle
from scanner.domain.common.rvol import classify, relative_volume
from scanner.domain.momentum import (
    detect_compression,
    detect_range_expansion,
    momentum_phase,
    momentum_score,
)
from scanner.domain.volume import (
    cross_validate_abnormal_volume,
    detect_contraction,
    detect_expansion,
    detect_volume_spike,
)
from scanner.shared import Timeframe

PARTICIPATION_ALGO_VERSION = "s7-participation-v2"


@dataclass(frozen=True, slots=True)
class ParticipationReplayReport:
    symbol: str
    timeframe: Timeframe
    candles: int
    volume_spikes: int
    suspect_volume: int
    expansions: int
    contractions: int
    range_expansions: int
    compressions: int
    accelerations: int
    exhaustion_watches: int
    events_inserted: int


class ParticipationReplayService:
    """Replay §6 and §7 across a window, recording their facts."""

    def __init__(
        self,
        candles: CandleRepository,
        events: EngineEventRepository,
        clock: Clock,
        *,
        algo_version: str = PARTICIPATION_ALGO_VERSION,
    ) -> None:
        self._candles = candles
        self._events = events
        self._clock = clock
        self._algo_version = algo_version

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> ParticipationReplayReport:
        if end <= start:
            raise ValueError("end must be greater than start")

        series = list(await self._candles.fetch_series(symbol, timeframe, start, end))

        report = _Counters()

        if not series:
            return report.finish(symbol, timeframe, 0)

        for index, candle in enumerate(series):
            await self._record_volume(symbol, timeframe, series, index, candle, report)
            await self._record_momentum(symbol, timeframe, series, index, candle, report)

        return report.finish(symbol, timeframe, len(series))

    async def _record_volume(
        self,
        symbol: str,
        timeframe: Timeframe,
        series: list[Candle],
        index: int,
        candle: Candle,
        report: _Counters,
    ) -> None:
        # §6.4 keys on the ABNORMAL *class*, not on a spike. `detect_volume_spike`
        # additionally requires §6.2's absolute quote floor, so an abnormal candle
        # on a thin symbol produces no spike -- and that is exactly the candle
        # §6.4 exists to cross-examine.
        check = cross_validate_abnormal_volume(
            series,
            index,
            # `market.liquidity_history` is empty until the daily universe job
            # has run, so the depth half reports None rather than a verdict.
            # Passing the repository today would read the same empty history;
            # it arrives with §6.6, which needs it for its own depth test.
            depth=None,
            median_depth_7d=None,
        )

        if check is not None and check.suspect:
            report.suspect_volume += 1

            await self._emit(
                symbol,
                timeframe,
                "VOLUME_SUSPECT",
                candle,
                {
                    "rvol": str(relative_volume(series, index)),
                    "participants_ok": check.participants_ok,
                    "depth_ok": check.depth_ok,
                    # False whenever a test could not be run. §6.4 gates the
                    # positive contribution on the check completing, so a
                    # reader must be able to tell a clean bill from a partial
                    # one.
                    "validated": check.validated,
                },
                report,
            )

        spike = detect_volume_spike(series, index)

        if spike is not None:
            report.volume_spikes += 1

            await self._emit(
                symbol,
                timeframe,
                "VOLUME_SPIKE",
                candle,
                {
                    "rvol": str(spike.rvol),
                    "rvol_class": spike.rvol_class.value,
                    "quote_volume": str(spike.quote_volume),
                    "direction": spike.direction,
                    "conviction": spike.conviction,
                    "absorption_candidate": spike.absorption_candidate,
                },
                report,
            )

        if detect_expansion(series, index):
            report.expansions += 1

            await self._emit(
                symbol, timeframe, "VOLUME_EXPANSION", candle, self._rvol(series, index), report
            )

        if detect_contraction(series, index):
            report.contractions += 1

            await self._emit(
                symbol, timeframe, "VOLUME_CONTRACTION", candle, self._rvol(series, index), report
            )

    async def _record_momentum(
        self,
        symbol: str,
        timeframe: Timeframe,
        series: list[Candle],
        index: int,
        candle: Candle,
        report: _Counters,
    ) -> None:
        if detect_range_expansion(series, index):
            report.range_expansions += 1

            await self._emit(symbol, timeframe, "RANGE_EXPANSION", candle, {}, report)

        if detect_compression(series, index):
            report.compressions += 1

            await self._emit(symbol, timeframe, "COMPRESSION", candle, {}, report)

        phase = momentum_phase(series, index)

        if phase is None:
            return

        score = momentum_score(series, index)

        # Only phase changes are recorded, not every reading. A score on every
        # candle is a series, and a series belongs in a chart query rather than
        # in the event log -- §7.2's *transitions* are the facts.
        if phase.accelerating:
            report.accelerations += 1

            await self._emit(
                symbol,
                timeframe,
                "MOMENTUM_ACCELERATING",
                candle,
                {
                    "accel": str(phase.accel),
                    "score": str(score.score) if score else None,
                    "direction": score.direction.value if score else None,
                },
                report,
            )

        if phase.exhaustion_watch:
            report.exhaustion_watches += 1

            await self._emit(
                symbol,
                timeframe,
                "EXHAUSTION_WATCH",
                candle,
                {
                    "accel": str(phase.accel),
                    "score": str(score.score) if score else None,
                },
                report,
            )

    def _rvol(self, series: list[Candle], index: int) -> dict[str, object]:
        value = relative_volume(series, index)
        band = classify(value)

        return {
            "rvol": str(value) if value is not None else None,
            "rvol_class": band.value if band is not None else None,
        }

    async def _emit(
        self,
        symbol: str,
        timeframe: Timeframe,
        event_type: str,
        candle: Candle,
        payload: dict[str, object],
        report: _Counters,
    ) -> None:
        inserted = await self._events.append(
            EngineEventRecord(
                event_key=build_event_key(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_at=candle.open_time,
                    algo_version=self._algo_version,
                ),
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                event_at=candle.open_time,
                algo_version=self._algo_version,
                payload=json.dumps(payload, sort_keys=True),
                created_at=self._clock.now(),
            )
        )

        if inserted:
            report.events_inserted += 1


@dataclass
class _Counters:
    volume_spikes: int = 0
    suspect_volume: int = 0
    expansions: int = 0
    contractions: int = 0
    range_expansions: int = 0
    compressions: int = 0
    accelerations: int = 0
    exhaustion_watches: int = 0
    events_inserted: int = 0

    def finish(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: int,
    ) -> ParticipationReplayReport:
        return ParticipationReplayReport(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            volume_spikes=self.volume_spikes,
            suspect_volume=self.suspect_volume,
            expansions=self.expansions,
            contractions=self.contractions,
            range_expansions=self.range_expansions,
            compressions=self.compressions,
            accelerations=self.accelerations,
            exhaustion_watches=self.exhaustion_watches,
            events_inserted=self.events_inserted,
        )
