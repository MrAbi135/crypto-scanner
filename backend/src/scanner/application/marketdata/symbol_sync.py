"""Symbol registry sync (Roadmap S1; SLS §1 registry facts only).

Universe tiering/quarantine mechanics are S3; here we mirror the venue's
USDT spot registry into DDD T1 with honest lifecycle mapping.
"""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.ports import Clock, MarketDataProvider, SymbolRepository
from scanner.domain.common import Symbol, SymbolStatus
from scanner.shared import new_ulid

_VENUE = "binance"
_QUOTE = "USDT"


@dataclass(frozen=True, slots=True)
class SymbolSyncReport:
    seen: int
    eligible: int
    upserted: int


class SymbolSyncService:
    def __init__(
        self, provider: MarketDataProvider, symbols: SymbolRepository, clock: Clock
    ) -> None:
        self._provider = provider
        self._symbols = symbols
        self._clock = clock

    async def sync(self) -> SymbolSyncReport:
        infos = await self._provider.fetch_symbols()
        now = self._clock.now()
        eligible = [i for i in infos if i.quote_asset == _QUOTE]
        rows = [
            Symbol(
                id=new_ulid(),
                venue=_VENUE,
                exchange_symbol=info.exchange_symbol,
                base_asset=info.base_asset,
                quote_asset=info.quote_asset,
                # SLS §1: a symbol enters QUARANTINE and earns ACTIVE via the
                # S3 universe manager; a non-trading symbol is DELISTED.
                status=SymbolStatus.QUARANTINE if info.trading else SymbolStatus.DELISTED,
                first_seen_at=now,
            )
            for info in eligible
        ]
        upserted = await self._symbols.upsert_many(rows)
        return SymbolSyncReport(seen=len(infos), eligible=len(eligible), upserted=upserted)
