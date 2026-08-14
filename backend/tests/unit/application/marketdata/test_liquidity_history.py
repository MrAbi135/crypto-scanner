"""Unit tests for seven-day liquidity history aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.marketdata.liquidity_history import (
    InsufficientLiquidityHistoryError,
    LiquiditySnapshotBuilder,
    build_liquidity_snapshot,
)
from scanner.application.ports.liquidity_history import (
    LiquidityHistoryRecord,
)


def record(
    *,
    day: int,
    volume: str,
    spread: str,
    depth: str,
) -> LiquidityHistoryRecord:
    return LiquidityHistoryRecord(
        exchange_symbol="BTCUSDT",
        observed_at=datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        )
        + timedelta(days=day),
        daily_quote_volume=Decimal(volume),
        spread_bps=Decimal(spread),
        depth_2pct=Decimal(depth),
    )


def seven_records() -> list[LiquidityHistoryRecord]:
    return [
        record(
            day=0,
            volume="10",
            spread="7",
            depth="70",
        ),
        record(
            day=1,
            volume="20",
            spread="6",
            depth="60",
        ),
        record(
            day=2,
            volume="30",
            spread="5",
            depth="50",
        ),
        record(
            day=3,
            volume="40",
            spread="4",
            depth="40",
        ),
        record(
            day=4,
            volume="50",
            spread="3",
            depth="30",
        ),
        record(
            day=5,
            volume="60",
            spread="2",
            depth="20",
        ),
        record(
            day=6,
            volume="70",
            spread="1",
            depth="10",
        ),
    ]


def test_build_snapshot_uses_exact_seven_day_medians() -> None:
    snapshot = build_liquidity_snapshot(seven_records())

    assert snapshot.median_daily_quote_volume == Decimal("40")
    assert snapshot.median_spread_bps == Decimal("4")
    assert snapshot.median_depth_2pct == Decimal("40")


def test_build_snapshot_rejects_less_than_seven_records() -> None:
    with pytest.raises(
        InsufficientLiquidityHistoryError,
        match="at least 7 daily liquidity observations are required",
    ):
        build_liquidity_snapshot(seven_records()[:6])


def test_build_snapshot_ignores_records_after_first_seven() -> None:
    records = seven_records()

    records.append(
        record(
            day=7,
            volume="9999",
            spread="9999",
            depth="9999",
        )
    )

    snapshot = build_liquidity_snapshot(records)

    assert snapshot.median_daily_quote_volume == Decimal("40")
    assert snapshot.median_spread_bps == Decimal("4")
    assert snapshot.median_depth_2pct == Decimal("40")


class FakeLiquidityHistoryRepository:
    def __init__(
        self,
        records: list[LiquidityHistoryRecord],
    ) -> None:
        self.records = records
        self.calls: list[tuple[str, int]] = []

    async def fetch_recent(
        self,
        exchange_symbol: str,
        *,
        limit: int = 7,
    ) -> list[LiquidityHistoryRecord]:
        self.calls.append(
            (
                exchange_symbol,
                limit,
            )
        )

        return self.records[:limit]


@pytest.mark.asyncio
async def test_builder_loads_seven_recent_records() -> None:
    repo = FakeLiquidityHistoryRepository(seven_records())

    builder = LiquiditySnapshotBuilder(repo)

    snapshot = await builder.build("BTCUSDT")

    assert repo.calls == [
        (
            "BTCUSDT",
            7,
        )
    ]

    assert snapshot.median_daily_quote_volume == Decimal("40")
    assert snapshot.median_spread_bps == Decimal("4")
    assert snapshot.median_depth_2pct == Decimal("40")


@pytest.mark.asyncio
async def test_builder_propagates_insufficient_history() -> None:
    repo = FakeLiquidityHistoryRepository(seven_records()[:5])

    builder = LiquiditySnapshotBuilder(repo)

    with pytest.raises(
        InsufficientLiquidityHistoryError,
    ):
        await builder.build("BTCUSDT")
