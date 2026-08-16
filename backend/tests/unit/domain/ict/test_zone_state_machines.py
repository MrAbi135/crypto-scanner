"""Exhaustive core state-machine tests for ICT zones."""

from __future__ import annotations

import pytest

from scanner.domain.ict import (
    FvgState,
    FvgStateMachine,
    IfvgState,
    IfvgStateMachine,
    ZoneState,
    ZoneStateMachine,
)


def test_standard_zone_happy_path() -> None:
    machine = ZoneStateMachine()

    assert machine.tested() is ZoneState.TESTED
    assert machine.mitigated() is ZoneState.MITIGATED
    assert machine.invalidated() is ZoneState.INVALIDATED


def test_fresh_zone_can_be_directly_mitigated() -> None:
    machine = ZoneStateMachine()

    assert machine.mitigated() is ZoneState.MITIGATED


def test_invalidated_zone_is_terminal() -> None:
    machine = ZoneStateMachine()

    machine.invalidated()

    with pytest.raises(ValueError):
        machine.tested()


def test_expired_zone_is_terminal() -> None:
    machine = ZoneStateMachine()

    machine.expired()

    with pytest.raises(ValueError):
        machine.mitigated()


def test_zone_cannot_regress_from_tested_to_tested() -> None:
    machine = ZoneStateMachine()

    machine.tested()

    with pytest.raises(ValueError):
        machine.tested()


def test_fvg_open_to_touched_to_ce_to_filled() -> None:
    machine = FvgStateMachine()

    assert machine.touched() is FvgState.TOUCHED
    assert machine.ce_filled() is FvgState.CE_FILLED
    assert machine.filled() is FvgState.FILLED


def test_fvg_can_invert_directly_from_open() -> None:
    machine = FvgStateMachine()

    assert machine.inverted() is FvgState.INVERTED


def test_filled_fvg_is_terminal() -> None:
    machine = FvgStateMachine()

    machine.filled()

    with pytest.raises(ValueError):
        machine.inverted()


def test_inverted_fvg_is_terminal() -> None:
    machine = FvgStateMachine()

    machine.inverted()

    with pytest.raises(ValueError):
        machine.touched()


def test_ifvg_must_be_proven_before_tested() -> None:
    machine = IfvgStateMachine()

    with pytest.raises(ValueError):
        machine.tested()


def test_ifvg_happy_path() -> None:
    machine = IfvgStateMachine()

    assert machine.proven() is IfvgState.FRESH
    assert machine.tested() is IfvgState.TESTED
    assert machine.mitigated() is IfvgState.MITIGATED
    assert machine.dead() is IfvgState.DEAD


def test_dead_ifvg_is_terminal() -> None:
    machine = IfvgStateMachine()

    machine.dead()

    with pytest.raises(ValueError):
        machine.proven()


def test_unproven_ifvg_can_expire() -> None:
    machine = IfvgStateMachine()

    assert machine.expired() is IfvgState.EXPIRED
