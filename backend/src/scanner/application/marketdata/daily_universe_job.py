"""Daily S3 universe evaluation workflow."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.marketdata.liquidity_collector import (
    DailyLiquidityCollector,
)
from scanner.application.marketdata.liquidity_history import (
    InsufficientLiquidityHistoryError,
    LiquiditySnapshotBuilder,
)
from scanner.application.marketdata.universe_manager import (
    UniverseEvaluationReport,
    UniverseManager,
)


@dataclass(frozen=True, slots=True)
class DailyUniverseJobReport:
    """Result of one symbol's daily universe evaluation."""

    exchange_symbol: str
    observation_saved: bool
    evaluation: UniverseEvaluationReport | None


class DailyUniverseJob:
    """Collect liquidity, build 7d medians and evaluate universe tier."""

    def __init__(
        self,
        collector: DailyLiquidityCollector,
        snapshots: LiquiditySnapshotBuilder,
        manager: UniverseManager,
    ) -> None:
        self._collector = collector
        self._snapshots = snapshots
        self._manager = manager

    async def run_symbol(
        self,
        exchange_symbol: str,
    ) -> DailyUniverseJobReport:
        """Run the complete daily S3 workflow for one symbol."""

        await self._collector.collect(exchange_symbol)

        try:
            snapshot = await self._snapshots.build(exchange_symbol)
        except InsufficientLiquidityHistoryError:
            return DailyUniverseJobReport(
                exchange_symbol=exchange_symbol,
                observation_saved=True,
                evaluation=None,
            )

        evaluation = await self._manager.evaluate(
            exchange_symbol,
            snapshot,
        )

        return DailyUniverseJobReport(
            exchange_symbol=exchange_symbol,
            observation_saved=True,
            evaluation=evaluation,
        )
