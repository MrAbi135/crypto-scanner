"""Unit tests for the daily S3 universe workflow."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.application.marketdata.daily_universe_job import (
    DailyUniverseJob,
)
from scanner.application.marketdata.liquidity_history import (
    InsufficientLiquidityHistoryError,
)
from scanner.application.marketdata.universe import (
    LiquiditySnapshot,
)
from scanner.application.marketdata.universe_manager import (
    UniverseEvaluationReport,
)
from scanner.domain.common.universe import UniverseTier


class FakeCollector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def collect(
        self,
        exchange_symbol: str,
    ) -> None:
        self.calls.append(exchange_symbol)


class FakeSnapshotBuilder:
    def __init__(
        self,
        snapshot: LiquiditySnapshot | None,
    ) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    async def build(
        self,
        exchange_symbol: str,
    ) -> LiquiditySnapshot:
        self.calls.append(exchange_symbol)

        if self.snapshot is None:
            raise InsufficientLiquidityHistoryError("not enough history")

        return self.snapshot


class FakeUniverseManager:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                str,
                LiquiditySnapshot,
            ]
        ] = []

    async def evaluate(
        self,
        exchange_symbol: str,
        snapshot: LiquiditySnapshot,
    ) -> UniverseEvaluationReport:
        self.calls.append(
            (
                exchange_symbol,
                snapshot,
            )
        )

        return UniverseEvaluationReport(
            exchange_symbol=exchange_symbol,
            observed_tier=UniverseTier.T1,
            previous_tier=UniverseTier.T2,
            current_tier=UniverseTier.T2,
            candidate_tier=UniverseTier.T1,
            consecutive_passes=1,
            consecutive_failures=0,
        )


@pytest.mark.asyncio
async def test_job_collects_before_building_snapshot() -> None:
    collector = FakeCollector()

    snapshot = LiquiditySnapshot(
        median_daily_quote_volume=Decimal("150000000"),
        median_spread_bps=Decimal("1"),
        median_depth_2pct=Decimal("2000000"),
    )

    snapshots = FakeSnapshotBuilder(snapshot)
    manager = FakeUniverseManager()

    job = DailyUniverseJob(
        collector,
        snapshots,
        manager,
    )

    report = await job.run_symbol("BTCUSDT")

    assert collector.calls == ["BTCUSDT"]
    assert snapshots.calls == ["BTCUSDT"]
    assert manager.calls == [
        (
            "BTCUSDT",
            snapshot,
        )
    ]

    assert report.exchange_symbol == "BTCUSDT"
    assert report.observation_saved is True
    assert report.evaluation is not None


@pytest.mark.asyncio
async def test_job_skips_evaluation_until_seven_day_history_exists() -> None:
    collector = FakeCollector()
    snapshots = FakeSnapshotBuilder(None)
    manager = FakeUniverseManager()

    job = DailyUniverseJob(
        collector,
        snapshots,
        manager,
    )

    report = await job.run_symbol("BTCUSDT")

    assert collector.calls == ["BTCUSDT"]
    assert snapshots.calls == ["BTCUSDT"]
    assert manager.calls == []

    assert report.exchange_symbol == "BTCUSDT"
    assert report.observation_saved is True
    assert report.evaluation is None


@pytest.mark.asyncio
async def test_job_returns_universe_evaluation_report() -> None:
    snapshot = LiquiditySnapshot(
        median_daily_quote_volume=Decimal("50000000"),
        median_spread_bps=Decimal("4"),
        median_depth_2pct=Decimal("500000"),
    )

    manager = FakeUniverseManager()

    job = DailyUniverseJob(
        FakeCollector(),
        FakeSnapshotBuilder(snapshot),
        manager,
    )

    report = await job.run_symbol("ETHUSDT")

    assert report.evaluation is not None
    assert report.evaluation.exchange_symbol == "ETHUSDT"
    assert report.evaluation.observed_tier is UniverseTier.T1
    assert report.evaluation.previous_tier is UniverseTier.T2
    assert report.evaluation.current_tier is UniverseTier.T2
    assert report.evaluation.candidate_tier is UniverseTier.T1
    assert report.evaluation.consecutive_passes == 1
    assert report.evaluation.consecutive_failures == 0
