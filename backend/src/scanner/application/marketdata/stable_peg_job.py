"""SLS §1.6's automatic stablecoin classifier, run once a day per symbol.

Reads the last 30 closed daily candles from the venue rather than from the
local store: the engine ingests a handful of symbols, and this has to judge
every symbol the universe job watches. It is one klines request per symbol,
inside the same rate budget as the liquidity collector's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal

import structlog

from scanner.application.ports import Clock, MarketDataProvider
from scanner.application.ports.repositories import SymbolRepository
from scanner.domain.common.stable_peg import (
    PEG_WINDOW_DAYS,
    StableFlag,
    next_stable_flag,
    peg_deviation,
)
from scanner.shared import Timeframe

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StablePegReport:
    exchange_symbol: str
    closes: int
    deviation: Decimal | None
    previous: StableFlag | None
    flag: StableFlag | None


class StablePegJob:
    def __init__(
        self,
        market_data: MarketDataProvider,
        symbols: SymbolRepository,
        clock: Clock,
    ) -> None:
        self._market_data = market_data
        self._symbols = symbols
        self._clock = clock

    async def run_symbol(self, exchange_symbol: str) -> StablePegReport:
        now = self._clock.now().astimezone(UTC)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=PEG_WINDOW_DAYS)

        # [start, end): thirty closed days, never today's open candle.
        candles = await self._market_data.fetch_candles(
            exchange_symbol,
            Timeframe.D1,
            start,
            end,
            limit=PEG_WINDOW_DAYS,
        )

        deviation = peg_deviation([candle.close for candle in candles])
        previous = await self._symbols.get_stable_flag(exchange_symbol)
        flag = next_stable_flag(previous, deviation)

        await self._symbols.save_stable_peg(
            exchange_symbol,
            flag=flag,
            deviation=deviation,
            checked_at=now,
        )

        if flag is not previous:
            log.info(
                "stable_flag_raised" if flag is StableFlag.FLAGGED else "stable_flag_cleared",
                symbol=exchange_symbol,
                deviation=str(deviation),
            )

        return StablePegReport(
            exchange_symbol=exchange_symbol,
            closes=len(candles),
            deviation=deviation,
            previous=previous,
            flag=flag,
        )
