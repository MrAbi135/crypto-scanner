"""Symbol registry sync (Roadmap S1; SLS §1 registry facts only).

Universe tiering/quarantine mechanics are S3; here we mirror the venue's
USDT spot registry into DDD T1 with honest lifecycle mapping.
"""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.ports import Clock, MarketDataProvider, SymbolRepository
from scanner.domain.common import Symbol, SymbolStatus, exclusion_reason
from scanner.shared import new_ulid

_VENUE = "binance"
_QUOTE = "USDT"


@dataclass(frozen=True, slots=True)
class SymbolSyncReport:
    seen: int
    eligible: int
    upserted: int
    excluded: int = 0


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

        # Every asset on the venue, not only USDT bases: §1.7's naming rule asks
        # whether a name is another listed asset plus a suffix.
        assets = {i.base_asset for i in infos} | {i.quote_asset for i in infos}

        rows = []

        for info in eligible:
            reason = exclusion_reason(
                info.base_asset, leveraged_flag=info.leveraged, venue_assets=assets
            )

            # SLS §1.3 evaluates the hard exclusions before anything else, so
            # an excluded symbol is EXCLUDED whether or not it trades. Otherwise
            # a symbol enters QUARANTINE and earns ACTIVE via the S3 universe
            # manager; a non-trading symbol is DELISTED.
            if reason is not None:
                status = SymbolStatus.EXCLUDED
            elif info.trading:
                status = SymbolStatus.QUARANTINE
            else:
                status = SymbolStatus.DELISTED

            rows.append(
                Symbol(
                    id=new_ulid(),
                    venue=_VENUE,
                    exchange_symbol=info.exchange_symbol,
                    base_asset=info.base_asset,
                    quote_asset=info.quote_asset,
                    status=status,
                    first_seen_at=now,
                    exclusion_reason=reason,
                )
            )

        upserted = await self._symbols.upsert_many(rows)
        return SymbolSyncReport(
            seen=len(infos),
            eligible=len(eligible),
            upserted=upserted,
            excluded=sum(1 for row in rows if row.status is SymbolStatus.EXCLUDED),
        )
