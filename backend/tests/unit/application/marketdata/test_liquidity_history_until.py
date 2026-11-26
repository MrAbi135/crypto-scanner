"""`fetch_until`, the point-in-time read behind §6.4's depth input.

The in-memory twin used by the golden harness; the Pg repository runs the
same assertions in tests/integration/test_persistence_pg.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.application.ports.liquidity_history import LiquidityHistoryRecord
from tests.golden.harness.memory import InMemoryLiquidityHistoryRepository

D0 = datetime(2026, 10, 1, tzinfo=UTC)


def record(symbol: str, day: int, depth: str = "100") -> LiquidityHistoryRecord:
    return LiquidityHistoryRecord(
        exchange_symbol=symbol,
        observed_at=D0 + timedelta(days=day),
        daily_quote_volume=Decimal("1"),
        spread_bps=Decimal("1"),
        depth_2pct=Decimal(depth),
    )


async def test_fetch_until_returns_nothing_stamped_after_until_newest_first() -> None:
    repo = InMemoryLiquidityHistoryRepository([record("AUSDT", d) for d in range(10)])

    rows = await repo.fetch_until("AUSDT", D0 + timedelta(days=5), limit=3)

    assert [r.observed_at for r in rows] == [D0 + timedelta(days=d) for d in (5, 4, 3)]


async def test_fetch_until_is_per_symbol_and_honours_the_limit() -> None:
    repo = InMemoryLiquidityHistoryRepository(
        [record("AUSDT", d) for d in range(10)] + [record("BUSDT", 9)]
    )

    rows = await repo.fetch_until("AUSDT", D0 + timedelta(days=30), limit=7)

    assert len(rows) == 7
    assert {r.exchange_symbol for r in rows} == {"AUSDT"}
    assert await repo.fetch_until("AUSDT", D0 + timedelta(days=30), limit=0) == []
    assert await repo.fetch_until("AUSDT", D0 - timedelta(days=1)) == []
