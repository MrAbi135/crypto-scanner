"""Event envelope base (S0.3 §2).

A pure, transport-blind frozen record. `event_id` and `occurred_at` are supplied
by the caller (which owns a clock) — this module reads no wall clock. Payload
values may include Decimal (money), which serializes to a canonical string
(API §5 numbers-as-strings); serialization never emits a float.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.shared.decimal_math import to_canonical_str
from scanner.shared.guards import require
from scanner.shared.types import Ulid


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """A transport-blind event record. Immutable by construction."""

    event_type: str  # dot-namespaced: area.subject.verb (API §14)
    event_id: Ulid  # ULID
    occurred_at: datetime  # timezone-aware UTC
    payload: Mapping[str, object]
    schema_version: int = 1
    flow_id: str | None = None

    def __post_init__(self) -> None:
        require(
            bool(self.event_type.strip()) and " " not in self.event_type,
            "EVENT_TYPE_INVALID",
            "event_type must be a non-empty key without whitespace",
        )
        require(
            "." in self.event_type,
            "EVENT_TYPE_NOT_NAMESPACED",
            "event_type must be dot-namespaced (area.subject.verb)",
        )
        require(
            self.occurred_at.tzinfo is not None,
            "EVENT_TS_NAIVE",
            "occurred_at must be timezone-aware (UTC)",
        )
        require(self.schema_version >= 1, "EVENT_SCHEMA_VERSION", "schema_version must be >= 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "schema_version": self.schema_version,
            "flow_id": self.flow_id,
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, compact, Decimal/datetime as strings."""
        return json.dumps(self.to_dict(), default=_encode, sort_keys=True, separators=(",", ":"))


def _encode(value: object) -> str:
    if isinstance(value, Decimal):
        return to_canonical_str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__} in an event payload")
