"""Shared platform primitives (TAD §15).

The only sanctioned import surface for shared code. Environment-blind,
I/O-free, stdlib-only — importable from any layer including domain.
"""

from scanner.shared.constants import DECIMAL_CONTEXT, UTC
from scanner.shared.decimal_math import (
    dec,
    parse_decimal,
    quantize_step,
    to_canonical_str,
)
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
from scanner.shared.events import EventEnvelope
from scanner.shared.guards import ensure_not_none, ensure_range, require
from scanner.shared.ids import as_ulid, monotonic_factory, new_ulid, parse_ulid
from scanner.shared.result import Err, Ok, Result
from scanner.shared.timeutil import (
    Timeframe,
    floor_to_boundary,
    is_boundary,
    next_boundary,
    span_boundaries,
    utc_from_ms,
    utc_ms,
)
from scanner.shared.types import JsonValue, Milliseconds, Seconds, Ulid, non_empty_str

__all__ = [
    "DECIMAL_CONTEXT",
    "UTC",
    "AuthError",
    "ConflictError",
    "DomainInvariantError",
    "EntitlementError",
    "Err",
    "EventEnvelope",
    "ExternalError",
    "InfraError",
    "JsonValue",
    "Milliseconds",
    "NotFoundError",
    "Ok",
    "Result",
    "ScannerError",
    "Seconds",
    "Timeframe",
    "Ulid",
    "ValidationError",
    "as_ulid",
    "dec",
    "ensure_not_none",
    "ensure_range",
    "floor_to_boundary",
    "is_boundary",
    "monotonic_factory",
    "new_ulid",
    "next_boundary",
    "non_empty_str",
    "parse_decimal",
    "parse_ulid",
    "quantize_step",
    "require",
    "span_boundaries",
    "to_canonical_str",
    "utc_from_ms",
    "utc_ms",
]
