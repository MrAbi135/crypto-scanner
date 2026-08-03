"""Platform error taxonomy (TAD §16). Codes are API §7 stable identifiers."""

from __future__ import annotations

from typing import Any


class ScannerError(Exception):
    """Root of the platform taxonomy. Every error carries a stable code."""

    code: str = "INTERNAL"

    def __init__(
        self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message
        self.details = details or {}


class ValidationError(ScannerError):
    """Boundary input rejected before reaching domain logic."""

    code = "VALIDATION_FAILED"


class DomainInvariantError(ScannerError):
    """An impossible state was attempted — a defect by definition (TAD §16)."""

    code = "DOMAIN_INVARIANT"


class NotFoundError(ScannerError):
    code = "NOT_FOUND"


class ConflictError(ScannerError):
    code = "CONFLICT"


class AuthError(ScannerError):
    """Authentication failure (identity layer, from Sprint S10)."""

    code = "AUTH"


class EntitlementError(ScannerError):
    """Authorized identity lacks the required capability (from Sprint S10)."""

    code = "ENTITLEMENT"


class InfraError(ScannerError):
    """Own-infrastructure failure (DB, Redis)."""

    code = "INFRA"


class ExternalError(ScannerError):
    """Third-party failure (exchange, provider). Adapter retry policy applies."""

    code = "EXTERNAL"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.retryable = retryable
