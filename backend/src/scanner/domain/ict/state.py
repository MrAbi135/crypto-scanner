"""Shared ICT zone state machines (SLS §5.1-§5.9)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.domain.ict.model import (
    FvgState,
    IfvgState,
    ZoneState,
)

# §5.1 Performance: "zone set bounded at `P.ict.max_zones = 60` per symbol-TF
# (evict oldest EXPIRED first)". The bound is on the set the engine carries
# forward and scores against, the same reading §4.2's `MAX_POOLS` takes for the
# resting map -- a zone outside the newest 60 still exists and still
# transitions, it is simply not part of what §8 considers.
#
# "EXPIRED first" costs nothing to honour: every terminal state is already
# outside the live set, so eviction reaches the living only once the dead are
# gone, which is what the clause asks for.
MAX_ZONES = 60

_ZONE_TERMINAL = {
    ZoneState.INVALIDATED,
    ZoneState.EXPIRED,
}

_FVG_TERMINAL = {
    FvgState.FILLED,
    FvgState.INVERTED,
    FvgState.EXPIRED,
}

_IFVG_TERMINAL = {
    IfvgState.DEAD,
    IfvgState.EXPIRED,
}

# The three sets above as stored strings, for readers that hold a state column
# rather than a typed machine. §5 calls terminal states permanent -- "no
# resurrection" -- so a zone in one of these can never interact again.
#
# Kept here rather than restated at each call site: the interaction replay used
# to carry its own copy of the same five strings, and a sixth terminal state
# added to a machine would have reached one of them and not the other.
TERMINAL_ZONE_STATES: frozenset[str] = (
    frozenset(state.value for state in _ZONE_TERMINAL)
    | frozenset(state.value for state in _FVG_TERMINAL)
    | frozenset(state.value for state in _IFVG_TERMINAL)
)


@dataclass(slots=True)
class ZoneStateMachine:
    state: ZoneState = ZoneState.FRESH

    def tested(self) -> ZoneState:
        return self._transition(ZoneState.TESTED)

    def mitigated(self) -> ZoneState:
        return self._transition(ZoneState.MITIGATED)

    def invalidated(self) -> ZoneState:
        return self._transition(ZoneState.INVALIDATED)

    def expired(self) -> ZoneState:
        return self._transition(ZoneState.EXPIRED)

    def _transition(
        self,
        target: ZoneState,
    ) -> ZoneState:
        if self.state in _ZONE_TERMINAL:
            raise ValueError(
                f"terminal zone cannot transition {self.state.value} -> {target.value}"
            )

        allowed = {
            ZoneState.FRESH: {
                ZoneState.TESTED,
                ZoneState.MITIGATED,
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            },
            ZoneState.TESTED: {
                ZoneState.MITIGATED,
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            },
            ZoneState.MITIGATED: {
                ZoneState.INVALIDATED,
                ZoneState.EXPIRED,
            },
        }

        if target not in allowed.get(
            self.state,
            set(),
        ):
            raise ValueError(f"illegal zone transition {self.state.value} -> {target.value}")

        self.state = target
        return self.state


@dataclass(slots=True)
class FvgStateMachine:
    state: FvgState = FvgState.OPEN

    def touched(self) -> FvgState:
        return self._transition(FvgState.TOUCHED)

    def ce_filled(self) -> FvgState:
        return self._transition(FvgState.CE_FILLED)

    def filled(self) -> FvgState:
        return self._transition(FvgState.FILLED)

    def inverted(self) -> FvgState:
        return self._transition(FvgState.INVERTED)

    def expired(self) -> FvgState:
        return self._transition(FvgState.EXPIRED)

    def _transition(
        self,
        target: FvgState,
    ) -> FvgState:
        if self.state in _FVG_TERMINAL:
            raise ValueError(f"terminal FVG cannot transition {self.state.value} -> {target.value}")

        allowed = {
            FvgState.OPEN: {
                FvgState.TOUCHED,
                FvgState.CE_FILLED,
                FvgState.FILLED,
                FvgState.INVERTED,
                FvgState.EXPIRED,
            },
            FvgState.TOUCHED: {
                FvgState.CE_FILLED,
                FvgState.FILLED,
                FvgState.INVERTED,
                FvgState.EXPIRED,
            },
            FvgState.CE_FILLED: {
                FvgState.FILLED,
                FvgState.INVERTED,
                FvgState.EXPIRED,
            },
        }

        if target not in allowed.get(
            self.state,
            set(),
        ):
            raise ValueError(f"illegal FVG transition {self.state.value} -> {target.value}")

        self.state = target
        return self.state


@dataclass(slots=True)
class IfvgStateMachine:
    state: IfvgState = IfvgState.UNPROVEN

    def proven(self) -> IfvgState:
        return self._transition(IfvgState.FRESH)

    def tested(self) -> IfvgState:
        return self._transition(IfvgState.TESTED)

    def mitigated(self) -> IfvgState:
        return self._transition(IfvgState.MITIGATED)

    def dead(self) -> IfvgState:
        return self._transition(IfvgState.DEAD)

    def expired(self) -> IfvgState:
        return self._transition(IfvgState.EXPIRED)

    def _transition(
        self,
        target: IfvgState,
    ) -> IfvgState:
        if self.state in _IFVG_TERMINAL:
            raise ValueError(
                f"terminal IFVG cannot transition {self.state.value} -> {target.value}"
            )

        allowed = {
            IfvgState.UNPROVEN: {
                IfvgState.FRESH,
                IfvgState.DEAD,
                IfvgState.EXPIRED,
            },
            IfvgState.FRESH: {
                IfvgState.TESTED,
                IfvgState.MITIGATED,
                IfvgState.DEAD,
                IfvgState.EXPIRED,
            },
            IfvgState.TESTED: {
                IfvgState.MITIGATED,
                IfvgState.DEAD,
                IfvgState.EXPIRED,
            },
            IfvgState.MITIGATED: {
                IfvgState.DEAD,
                IfvgState.EXPIRED,
            },
        }

        if target not in allowed.get(
            self.state,
            set(),
        ):
            raise ValueError(f"illegal IFVG transition {self.state.value} -> {target.value}")

        self.state = target
        return self.state
