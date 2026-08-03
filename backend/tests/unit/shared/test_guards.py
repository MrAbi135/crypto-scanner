"""Guard tests (S0.3 §2): raise exactly on false condition with stable codes."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.shared import DomainInvariantError, ensure_not_none, ensure_range, require


def test_require_passes_on_true() -> None:
    require(True, "OK", "should not raise")


def test_require_raises_with_code_on_false() -> None:
    with pytest.raises(DomainInvariantError) as excinfo:
        require(False, "BAD_STATE", "invariant broken")
    assert excinfo.value.code == "BAD_STATE"


def test_ensure_not_none_passes_through() -> None:
    assert ensure_not_none(5, "X", "m") == 5


def test_ensure_not_none_raises() -> None:
    with pytest.raises(DomainInvariantError):
        ensure_not_none(None, "IS_NONE", "m")


def test_ensure_range_returns_value_in_bounds() -> None:
    assert ensure_range(5, 1, 10, "R", "m") == 5
    assert ensure_range(Decimal("1.5"), Decimal("1"), Decimal("2"), "R", "m") == Decimal("1.5")


def test_ensure_range_rejects_below() -> None:
    with pytest.raises(DomainInvariantError):
        ensure_range(0, 1, 10, "R", "m")


def test_ensure_range_rejects_above() -> None:
    with pytest.raises(DomainInvariantError):
        ensure_range(11, 1, 10, "R", "m")
