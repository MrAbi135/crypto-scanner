"""Total, typed Result type (S0.2 exemplar — infrastructure, not business logic).

`Ok[T]` / `Err[E]` model success-or-failure without exceptions-as-control-flow.
This module also proves the pytest + hypothesis + mypy-strict loop end-to-end;
it carries 100% coverage by review rule (S0.1 §21).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, final


@final
@dataclass(frozen=True, slots=True)
class Ok[T]:
    """A successful result carrying a value of type ``T``."""

    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def map[U](self, fn: Callable[[T], U]) -> Ok[U]:
        """Transform the contained success value."""
        return Ok(fn(self.value))

    def map_err(self, fn: Callable[[Any], Any]) -> Ok[T]:
        """No-op on success — there is no error to transform."""
        return self

    def and_then[U, F](self, fn: Callable[[T], Result[U, F]]) -> Result[U, F]:
        """Chain a fallible computation onto the success value."""
        return fn(self.value)

    def unwrap_or(self, default: T) -> T:
        return self.value


@final
@dataclass(frozen=True, slots=True)
class Err[E]:
    """A failed result carrying an error of type ``E``."""

    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def map(self, fn: Callable[[Any], Any]) -> Err[E]:
        """No-op on failure — there is no success value to transform."""
        return self

    def map_err[F](self, fn: Callable[[E], F]) -> Err[F]:
        """Transform the contained error."""
        return Err(fn(self.error))

    def and_then(self, fn: Callable[[Any], Result[Any, E]]) -> Err[E]:
        """No-op on failure — the chain short-circuits."""
        return self

    def unwrap_or[T](self, default: T) -> T:
        return default


type Result[T, E] = Ok[T] | Err[E]
"""A value that is either ``Ok[T]`` or ``Err[E]``."""
