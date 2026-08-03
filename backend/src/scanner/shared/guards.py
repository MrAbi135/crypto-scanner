"""Pure invariant guards (S0.3 §2). Zero I/O, zero logging."""

from __future__ import annotations

from decimal import Decimal

from scanner.shared.errors import DomainInvariantError


def require(condition: bool, code: str, message: str) -> None:
    """Assert a domain invariant. Failure is a defect, not a user error."""
    if not condition:
        raise DomainInvariantError(message, code=code)


def ensure_not_none[T](value: T | None, code: str, message: str) -> T:
    if value is None:
        raise DomainInvariantError(message, code=code)
    return value


def ensure_range[Number: (int, Decimal)](
    value: Number, low: Number, high: Number, code: str, message: str
) -> Number:
    """Return ``value`` if within [low, high], else raise DomainInvariantError."""
    if value < low or value > high:
        raise DomainInvariantError(message, code=code)
    return value
