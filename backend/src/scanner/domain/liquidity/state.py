"""Liquidity pool lifecycle state machine (SLS §4.2/§4.8)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.domain.liquidity.model import PoolState

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
    max_age: int = 500,
) -> bool:
    if age_candles < 0:
        raise ValueError("age_candles must be non-negative")

    if max_age <= 0:
        raise ValueError("max_age must be positive")

    return age_candles > max_age
