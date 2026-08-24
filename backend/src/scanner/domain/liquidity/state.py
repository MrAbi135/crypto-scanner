"""Liquidity pool lifecycle state machine (SLS §4.2/§4.8)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.domain.liquidity.model import PoolState

# §4.2 Performance: "pool set per symbol-TF bounded (P.liquidity.max_pools =
# 40, evict lowest-strength expired first)". The bound is on the resting map
# §4.5 exposes, not on the rows: a pool outside the top 40 is still ACTIVE and
# still transitions, it is simply not part of the map that gets published.
MAX_POOLS = 40

# §4.2: pools expire past `P.liquidity.pool_max_age = 500` closed candles.
# Named for the same reason as §4.3's gaps: a parameter that exists only as
# a call default is invisible to the registry that checks Appendix A.
POOL_MAX_AGE = 500

_TERMINAL_STATES = {
    PoolState.SWEPT,
    PoolState.BROKEN,
    PoolState.EXPIRED,
}


@dataclass(slots=True)
class PoolStateMachine:
    state: PoolState = PoolState.ACTIVE

    def sweep(self) -> PoolState:
        return self._transition(PoolState.SWEPT)

    def break_pool(self) -> PoolState:
        return self._transition(PoolState.BROKEN)

    def expire(self) -> PoolState:
        return self._transition(PoolState.EXPIRED)

    def _transition(
        self,
        target: PoolState,
    ) -> PoolState:
        if self.state in _TERMINAL_STATES:
            raise ValueError(
                f"terminal liquidity pool cannot transition {self.state.value} -> {target.value}"
            )

        if self.state is not PoolState.ACTIVE:
            raise ValueError(f"unsupported pool state: {self.state.value}")

        self.state = target
        return self.state


def should_expire_pool(
    *,
    age_candles: int,
    max_age: int = POOL_MAX_AGE,
) -> bool:
    if age_candles < 0:
        raise ValueError("age_candles must be non-negative")

    if max_age <= 0:
        raise ValueError("max_age must be positive")

    return age_candles > max_age
