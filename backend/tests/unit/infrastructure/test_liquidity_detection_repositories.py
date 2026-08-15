"""Repository mapping tests for S5 liquidity persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from scanner.infrastructure.persistence.liquidity_detection_repositories import (
    _pool_record,
)
from scanner.shared import Timeframe


def test_pool_row_maps_to_record() -> None:
    now = datetime(
        2026,
        8,
        15,
        10,
        0,
        tzinfo=UTC,
    )

    row = SimpleNamespace(
        pool_id="pool-1",
        symbol="BTCUSDT",
        timeframe="M5",
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=Decimal("100"),
        band_low=Decimal("100"),
        band_high=Decimal("100"),
        strength=Decimal("75.5"),
        state="ACTIVE",
        member_count=1,
        created_index=10,
        created_at=now,
        updated_at=now,
        evidence='{"source":"test"}',
    )

    record = _pool_record(row)  # type: ignore[arg-type]

    assert record.pool_id == "pool-1"
    assert record.symbol == "BTCUSDT"
    assert record.timeframe is Timeframe.M5
    assert record.side == "BSL"
    assert record.strength == Decimal("75.5")
    assert record.state == "ACTIVE"
