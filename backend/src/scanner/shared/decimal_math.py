"""Decimal-exact numeric primitives — Constitution §45.8 made executable.

Floats are rejected at every entry point BY DESIGN: a float has already
lost the exactness this platform promises, so accepting one silently
would launder corruption into the record.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from scanner.shared.constants import DECIMAL_CONTEXT
from scanner.shared.errors import ValidationError

_ZERO = Decimal("0")


def parse_decimal(value: str | int | Decimal, *, field: str = "value") -> Decimal:
    """Parse an exact numeric input. Floats are a TypeError, not a conversion."""
    if isinstance(value, float):
        raise TypeError(
            f"{field}: floating-point input is prohibited (Constitution §45.8) — "
            "pass the exchange string"
        )
    if isinstance(value, Decimal):
        return value
    try:
        return DECIMAL_CONTEXT.create_decimal(value if isinstance(value, str) else str(value))
    except InvalidOperation as exc:
        raise ValidationError(f"{field}: not a valid decimal: {value!r}") from exc


def dec(value: str | int) -> Decimal:
    """Terse constructor for literals in code and tests."""
    return parse_decimal(value)


def quantize_step(value: Decimal, step: Decimal) -> Decimal:
    """Snap value onto an exchange step grid (price tick / qty step)."""
    if step <= _ZERO:
        raise ValidationError(f"step must be positive, got {step}")
    quotient = DECIMAL_CONTEXT.divide_int(value, step)
    return DECIMAL_CONTEXT.multiply(quotient, step)


def to_canonical_str(value: Decimal) -> str:
    """Canonical wire form (API §5): plain notation, no exponent, no trailing noise."""
    text = format(value.normalize(DECIMAL_CONTEXT), "f")
    return "0" if text in {"-0", "0E+0"} else text
