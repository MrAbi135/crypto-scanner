"""Symbol registry sync tests (Roadmap S1 / SLS §1)."""

from __future__ import annotations

from collections.abc import Sequence

from scanner.application.marketdata import SymbolSyncService
from scanner.application.ports.market_data_provider import ExchangeSymbolInfo
from scanner.domain.common import ExclusionReason, Symbol, SymbolStatus
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


def _info(
    symbol: str, base: str, quote: str, *, trading: bool = True, leveraged: bool = False
) -> ExchangeSymbolInfo:
    return ExchangeSymbolInfo(
        exchange_symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        trading=trading,
        leveraged=leveraged,
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


async def test_sync_excludes_before_anything_else() -> None:
    """SLS §1.3: exclusions are evaluated before tiers -- and before trading.

    USDCUSDT trades and was ACTIVE at T1; a delisted leveraged token is still
    a leveraged token, so it is EXCLUDED rather than DELISTED.
    """
    provider = _FakeProvider(
        [
            _info("BTCUSDT", "BTC", "USDT"),
            _info("USDCUSDT", "USDC", "USDT"),
            _info("EURUSDT", "EUR", "USDT"),
            _info("BTCUPUSDT", "BTCUP", "USDT", trading=False, leveraged=True),
            # Unflagged: caught by name, because ETH is an asset on the venue --
            # here only as the base of a non-USDT pair.
            _info("ETHBULLUSDT", "ETHBULL", "USDT", trading=False),
            _info("ETHBTC", "ETH", "BTC"),
            _info("JUPUSDT", "JUP", "USDT"),
            _info("PAXGUSDT", "PAXG", "USDT"),
        ]
    )
    repo = _FakeSymbolRepo()
    report = await SymbolSyncService(provider, repo, FakeClock()).sync()  # type: ignore[arg-type]

    saved = {s.exchange_symbol: (s.status, s.exclusion_reason) for s in repo.saved}

    assert saved == {
        "BTCUSDT": (SymbolStatus.QUARANTINE, None),
        "USDCUSDT": (SymbolStatus.EXCLUDED, ExclusionReason.STABLECOIN),
        "EURUSDT": (SymbolStatus.EXCLUDED, ExclusionReason.FIAT_PEGGED),
        "BTCUPUSDT": (SymbolStatus.EXCLUDED, ExclusionReason.LEVERAGED_TOKEN),
        "ETHBULLUSDT": (SymbolStatus.EXCLUDED, ExclusionReason.LEVERAGED_TOKEN),
        "JUPUSDT": (SymbolStatus.QUARANTINE, None),
        "PAXGUSDT": (SymbolStatus.QUARANTINE, None),
    }
    assert (report.eligible, report.excluded) == (7, 4)


async def test_sync_empty_registry() -> None:
    report = await SymbolSyncService(_FakeProvider([]), _FakeSymbolRepo(), FakeClock()).sync()  # type: ignore[arg-type]
    assert (report.seen, report.eligible, report.upserted) == (0, 0, 0)
