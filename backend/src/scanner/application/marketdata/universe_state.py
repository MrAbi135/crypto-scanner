"""Universe tier hysteresis state machine (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.application.marketdata.universe import UniverseTier

_PROMOTION_DAYS = 7
_DEMOTION_DAYS = 3

_TIER_RANK: dict[UniverseTier, int] = {
    UniverseTier.INELIGIBLE: 0,
    UniverseTier.T3: 1,
    UniverseTier.T2: 2,
    UniverseTier.T1: 3,
}


@dataclass(slots=True)
class UniverseTierState:
    """Track stable daily tier transitions with anti-flapping hysteresis."""

    current_tier: UniverseTier = UniverseTier.INELIGIBLE
    candidate_tier: UniverseTier | None = None
    consecutive_passes: int = 0
    consecutive_failures: int = 0

    def evaluate(self, observed_tier: UniverseTier) -> UniverseTier:
        """Apply one daily liquidity evaluation and return the stable tier."""

        if observed_tier is self.current_tier:
            self._reset_candidate()
            return self.current_tier

        current_rank = _TIER_RANK[self.current_tier]
        observed_rank = _TIER_RANK[observed_tier]

        if observed_rank > current_rank:
            return self._evaluate_promotion(observed_tier)

        return self._evaluate_demotion(observed_tier)

    def _evaluate_promotion(
        self,
        observed_tier: UniverseTier,
    ) -> UniverseTier:
        if self.candidate_tier is not observed_tier:
            self.candidate_tier = observed_tier
            self.consecutive_passes = 0

        self.consecutive_passes += 1
        self.consecutive_failures = 0

        if self.consecutive_passes >= _PROMOTION_DAYS:
            self.current_tier = observed_tier
            self._reset_candidate()

        return self.current_tier

    def _evaluate_demotion(
        self,
        observed_tier: UniverseTier,
    ) -> UniverseTier:
        if self.candidate_tier is not observed_tier:
            self.candidate_tier = observed_tier
            self.consecutive_failures = 0

        self.consecutive_failures += 1
        self.consecutive_passes = 0

        if self.consecutive_failures >= _DEMOTION_DAYS:
            self.current_tier = observed_tier
            self._reset_candidate()

        return self.current_tier

    def _reset_candidate(self) -> None:
        self.candidate_tier = None
        self.consecutive_passes = 0
        self.consecutive_failures = 0
