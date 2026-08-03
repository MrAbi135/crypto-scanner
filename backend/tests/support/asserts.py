"""Reusable contract assertions (S0.3 §3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest


def assert_frozen(instance: object, attr: str, value: object) -> None:
    """A frozen dataclass rejects attribute mutation."""
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attr, value)


def assert_roundtrips[T](
    serialize: Callable[[T], str], deserialize: Callable[[str], T], value: T
) -> None:
    assert deserialize(serialize(value)) == value
