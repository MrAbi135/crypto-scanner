"""Error taxonomy contracts (S0.2 §13). Downstream code depends on this shape."""

from __future__ import annotations

import pytest

from scanner.shared.errors import (
    AuthError,
    ConflictError,
    DomainInvariantError,
    EntitlementError,
    ExternalError,
    InfraError,
    NotFoundError,
    ScannerError,
    ValidationError,
)

_TAXONOMY = [
    (ValidationError, "VALIDATION_FAILED"),
    (DomainInvariantError, "DOMAIN_INVARIANT"),
    (NotFoundError, "NOT_FOUND"),
    (ConflictError, "CONFLICT"),
    (AuthError, "AUTH"),
    (EntitlementError, "ENTITLEMENT"),
    (InfraError, "INFRA"),
    (ExternalError, "EXTERNAL"),
]


@pytest.mark.parametrize(("err_cls", "code"), _TAXONOMY)
def test_codes_are_stable(err_cls: type[ScannerError], code: str) -> None:
    assert err_cls("boom").code == code


@pytest.mark.parametrize(("err_cls", "code"), _TAXONOMY)
def test_every_error_is_a_scanner_error(err_cls: type[ScannerError], code: str) -> None:
    assert issubclass(err_cls, ScannerError)
    assert isinstance(err_cls("boom"), ScannerError)


def test_details_default_empty() -> None:
    assert ScannerError("x").details == {}


def test_details_carried() -> None:
    assert ScannerError("x", details={"k": 1}).details == {"k": 1}


def test_code_can_be_overridden() -> None:
    assert ScannerError("x", code="CUSTOM").code == "CUSTOM"


def test_external_retryable_flag() -> None:
    assert ExternalError("x", retryable=True).retryable is True
    assert ExternalError("x").retryable is False
