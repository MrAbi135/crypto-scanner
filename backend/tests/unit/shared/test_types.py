"""Type primitive tests (S0.3 §2)."""

from __future__ import annotations

import pytest

from scanner.shared import ValidationError, non_empty_str
from scanner.shared.types import Milliseconds, Ulid


def test_non_empty_str_returns_value() -> None:
    assert non_empty_str("hello") == "hello"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_non_empty_str_rejects_blank(blank: str) -> None:
    with pytest.raises(ValidationError):
        non_empty_str(blank)


def test_newtypes_are_runtime_identity() -> None:
    assert Ulid("XYZ") == "XYZ"
    assert Milliseconds(5) == 5
