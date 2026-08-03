"""Decimal law tests (Constitution §45.8)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scanner.shared import ValidationError, dec, parse_decimal, quantize_step, to_canonical_str

_decimal_strings = st.decimals(
    allow_nan=False, allow_infinity=False, min_value=Decimal("-1e15"), max_value=Decimal("1e15")
).map(str)


def test_float_is_rejected_by_design() -> None:
    with pytest.raises(TypeError, match="floating-point input is prohibited"):
        parse_decimal(0.1)  # type: ignore[arg-type]


def test_garbage_is_validation_error() -> None:
    with pytest.raises(ValidationError):
        parse_decimal("not-a-number")


def test_decimal_passes_through_unchanged() -> None:
    value = Decimal("1.5")
    assert parse_decimal(value) is value


@given(_decimal_strings)
def test_roundtrip_identity(raw: str) -> None:
    value = parse_decimal(raw)
    assert parse_decimal(to_canonical_str(value)) == value


def test_canonical_form_is_plain() -> None:
    assert to_canonical_str(dec("1.2300")) == "1.23"
    assert to_canonical_str(dec("0.00000001")) == "0.00000001"  # no exponent notation
    assert to_canonical_str(dec("0")) == "0"


def test_quantize_step_snaps_down_to_grid() -> None:
    assert quantize_step(dec("100.007"), dec("0.01")) == dec("100.00")
    assert quantize_step(dec("0.123456"), dec("0.0001")) == dec("0.1234")


def test_quantize_step_rejects_nonpositive_step() -> None:
    with pytest.raises(ValidationError):
        quantize_step(dec("1"), dec("0"))


@given(_decimal_strings)
def test_quantize_is_idempotent(raw: str) -> None:
    step = dec("0.01")
    once = quantize_step(parse_decimal(raw), step)
    assert quantize_step(once, step) == once
