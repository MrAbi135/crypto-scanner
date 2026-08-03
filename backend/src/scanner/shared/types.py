"""Domain-agnostic type primitives (S0.3 §2).

Opaque id NewTypes, unit-tagged integers, a JSON value alias, and a
non-empty-string guard. No market semantics live here (that is `domain/common`,
Sprint S1). `float` never appears — JSON numbers are `int` or Decimal-as-string.
"""

from __future__ import annotations

from typing import NewType

from scanner.shared.errors import ValidationError

# Opaque identifiers — distinct at the type level, plain strings/ints at runtime.
Ulid = NewType("Ulid", str)

# Unit-tagged integers (avoid mixing millisecond and second scalars).
Milliseconds = NewType("Milliseconds", int)
Seconds = NewType("Seconds", int)

# Recursive JSON value alias (no float — decimal law).
type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def non_empty_str(value: str, *, field: str = "value") -> str:
    """Return ``value`` if it is a non-blank string, else raise ValidationError."""
    if not value.strip():
        raise ValidationError(f"{field}: must be a non-empty string")
    return value
