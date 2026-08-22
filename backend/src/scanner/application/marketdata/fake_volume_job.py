"""§6.6's daily fake-volume evaluation, per symbol.

Composes the four tests from what each engine already records, applies §6.6's
hysteresis, and persists the tag. What it does *not* do is guess: a test whose
input is absent stays `None` and scores nothing, and the report says which of
the four were actually asked.

Two of the four are answerable from data this build has had all along, and two
arrived with T4 and the universe job. The composite needs any two to fail, so
the tag is reachable on stored candles and §6.4 events alone -- and gets
sharper as the other inputs fill in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from scanner.application.ports.detection import EngineEventRepository
from scanner.application.ports.repositories import (
    SymbolRepository,
    TradeAggregateRepository,
)
from scanner.domain.common import TradeAggregate
from scanner.domain.volume import (
    FakeVolumeTests,
    WashRiskState,
    evaluate_wash_risk,
    excess_suspect_candles,
    fake_volume_score,
    round_trip_symmetry,
    tags_wash_risk,
    trade_size_uniformity,
)
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class FakeVolumeReport:
    exchange_symbol: str
    tests: FakeVolumeTests
    score: Decimal
    tagged_today: bool
    state: WashRiskState


class FakeVolumeJob:
    """One symbol's §6.6 evaluation for one closed UTC day."""

    def __init__(
        self,
        symbols: SymbolRepository,
        trades: TradeAggregateRepository,
        suspect_counts: SuspectVolumeCounter,
    ) -> None:
        self._symbols = symbols
        self._trades = trades
        self._suspect_counts = suspect_counts

    async def run_symbol(
        self,
        exchange_symbol: str,
        day_start: datetime,
        *,
        daily_delta: Decimal | None = None,
        daily_volume: Decimal | None = None,
        rvol_elevated: bool = False,
    ) -> FakeVolumeReport:
        minutes = await self._trades.list_between(
            exchange_symbol,
            day_start,
            day_start + _DAY,
        )

        tests = FakeVolumeTests(
            # (1) needs the universe-wide volume/depth percentile, which needs
            # `market.liquidity_history` populated -- the daily universe job
            # only became able to fill it once symbols could reach ACTIVE.
            volume_unsupported_by_depth=None,
            round_trip_symmetry=(
                round_trip_symmetry(
                    absolute_delta=abs(daily_delta),
                    total_volume=daily_volume,
                    rvol_elevated=rvol_elevated,
                )
                if daily_delta is not None and daily_volume is not None
                else None
            ),
            trade_size_uniformity=_uniformity(minutes),
            excess_suspect_candles=excess_suspect_candles(
                await self._suspect_counts.count(exchange_symbol, day_start, day_start + _DAY)
            ),
        )

        tagged_today = tags_wash_risk(tests)

        state = evaluate_wash_risk(
            await self._symbols.get_wash_risk(exchange_symbol),
            tagged_today,
        )

        await self._symbols.save_wash_risk(exchange_symbol, state)

        log.info(
            "fake_volume_evaluated",
            symbol=exchange_symbol,
            day=day_start.date().isoformat(),
            tests_measured=tests.measured,
            tests_failed=tests.failed,
            score=str(fake_volume_score(tests)),
            tagged_today=tagged_today,
            wash_risk=state.tagged,
            clean_days=state.clean_days,
        )

        return FakeVolumeReport(
            exchange_symbol=exchange_symbol,
            tests=tests,
            score=fake_volume_score(tests),
            tagged_today=tagged_today,
            state=state,
        )


class SuspectVolumeCounter:
    """§6.6(4): how many §6.4 candles the symbol produced in the day.

    Across every scanned timeframe, not one. §6.6 counts "`suspect_volume`
    candle count > 5 in 24h" for the *symbol*, and a symbol scanned on four
    timeframes produces suspect candles on all four.
    """

    def __init__(
        self,
        events: EngineEventRepository,
        timeframes: tuple[Timeframe, ...],
    ) -> None:
        self._events = events
        self._timeframes = timeframes

    async def count(self, symbol: str, start: datetime, end: datetime) -> int:
        total = 0

        for timeframe in self._timeframes:
            records = await self._events.list_events(symbol, timeframe, start, end)

            total += sum(1 for record in records if record.event_type == "VOLUME_SUSPECT")

        return total


def _uniformity(minutes: Sequence[TradeAggregate]) -> bool | None:
    """§6.6(3) over a whole day of minute buckets.

    The day's mean is the print-weighted mean of the minutes'. Its dispersion
    is the pooled one: within-minute variance plus the variance *between* the
    minute means, which a plain average of the per-minute stddevs would throw
    away -- and throwing it away would make a day of wildly different minutes
    look as uniform as a day of identical ones.
    """
    if not minutes:
        return None

    prints = sum(item.trade_count for item in minutes)

    if prints <= 0:
        return None

    total = Decimal(prints)

    mean = sum((m.mean_trade_size * Decimal(m.trade_count) for m in minutes), Decimal(0)) / total

    pooled = (
        sum(
            (
                Decimal(m.trade_count) * (m.stddev_trade_size**2 + (m.mean_trade_size - mean) ** 2)
                for m in minutes
            ),
            Decimal(0),
        )
        / total
    )

    return trade_size_uniformity(mean_trade_size=mean, stddev_trade_size=pooled.sqrt())
