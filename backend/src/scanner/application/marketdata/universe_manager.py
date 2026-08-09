"""Universe manager orchestration (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.marketdata.universe import (
    LiquiditySnapshot,
    classify_tier,
)
from scanner.application.marketdata.universe_state import UniverseTierState
from scanner.application.ports import (
    SymbolRepository,
    UniverseStateRecord,
)
from scanner.domain.common.universe import UniverseTier


@dataclass(frozen=True, slots=True)
class UniverseEvaluationReport:
    """Result of one daily liquidity evaluation for one symbol."""

    exchange_symbol: str
    observed_tier: UniverseTier
    previous_tier: UniverseTier
    current_tier: UniverseTier
    candidate_tier: UniverseTier | None
    consecutive_passes: int
    consecutive_failures: int

    @property
    def tier_changed(self) -> bool:
        """Whether the stable tier changed during this evaluation."""
        return self.current_tier is not self.previous_tier


class UniverseManager:
    """Apply daily liquidity classification with persistent hysteresis."""

    def __init__(
        self,
        symbols: SymbolRepository,
    ) -> None:
        self._symbols = symbols

    async def evaluate(
        self,
        exchange_symbol: str,
        snapshot: LiquiditySnapshot,
    ) -> UniverseEvaluationReport:
        """Evaluate and persist one symbol's daily universe state."""

        persisted = await self._symbols.get_universe_state(
            exchange_symbol,
        )

        if persisted is None:
            raise LookupError(
                f"Unknown symbol: {exchange_symbol}"
            )

        observed_tier = classify_tier(snapshot)
        previous_tier = persisted.tier

        state = UniverseTierState(
            current_tier=persisted.tier,
            candidate_tier=persisted.candidate_tier,
            consecutive_passes=persisted.consecutive_passes,
            consecutive_failures=persisted.consecutive_failures,
        )

        current_tier = state.evaluate(observed_tier)

        updated = UniverseStateRecord(
            exchange_symbol=exchange_symbol,
            tier=current_tier,
            candidate_tier=state.candidate_tier,
            consecutive_passes=state.consecutive_passes,
            consecutive_failures=state.consecutive_failures,
        )

        await self._symbols.save_universe_state(updated)

        return UniverseEvaluationReport(
            exchange_symbol=exchange_symbol,
            observed_tier=observed_tier,
            previous_tier=previous_tier,
            current_tier=current_tier,
            candidate_tier=state.candidate_tier,
            consecutive_passes=state.consecutive_passes,
            consecutive_failures=state.consecutive_failures,
        )
