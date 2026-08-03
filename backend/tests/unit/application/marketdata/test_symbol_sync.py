"""Symbol registry sync tests (Roadmap S1 / SLS §1)."""

from __future__ import annotations

from collections.abc import Sequence

from scanner.application.marketdata import SymbolSyncService
from scanner.application.ports.market_data_provider import ExchangeSymbolInfo
from scanner.domain.common import Symbol, SymbolStatus
from tests.support.clock import FakeClock


class _FakeProvider:
    def __init__(self, infos: list[ExchangeSymbolInfo]) -> None:
        self._infos = infos

    async def fetch_symbols(self) -> Sequence[ExchangeSymbolInfo]:
        return self._infos


class _FakeSymbolRepo:
    def __init__(self) -> None:
        self.saved: list[Symbol] = []

    async def upsert_many(self, symbols: Sequence[Symbol]) -> int:
        self.saved = list(symbols)
        return len(self.saved)


def _info(symbol: str, base: str, quote: str, *, trading: bool = True) -> ExchangeSymbolInfo:
    return ExchangeSymbolInfo(
        exchange_symbol=symbol, base_asset=base, quote_asset=quote, trading=trading
    )


async def test_sync_mirrors_only_usdt_and_maps_lifecycle() -> None:
    provider = _FakeProvider(
        [
            _info("BTCUSDT", "BTC", "USDT"),
            _info("ETHBTC", "ETH", "BTC"),  # non-USDT quote → excluded
            _info("XRPUSDT", "XRP", "USDT", trading=False),  # not trading → DELISTED
        ]
    )
    repo = _FakeSymbolRepo()
    report = await SymbolSyncService(provider, repo, FakeClock()).sync()  # type: ignore[arg-type]

    assert report.seen == 3
    assert report.eligible == 2
    assert report.upserted == 2
    by_symbol = {s.exchange_symbol: s.status for s in repo.saved}
    assert by_symbol["BTCUSDT"] == SymbolStatus.QUARANTINE
    assert by_symbol["XRPUSDT"] == SymbolStatus.DELISTED


async def test_sync_empty_registry() -> None:
    report = await SymbolSyncService(_FakeProvider([]), _FakeSymbolRepo(), FakeClock()).sync()  # type: ignore[arg-type]
    assert (report.seen, report.eligible, report.upserted) == (0, 0, 0)
