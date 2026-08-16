"""Canonical serialisation for golden comparison and determinism hashing.

Constitution §29.3 and SLS §0 require byte-identical detector output for
identical input. "Byte-identical" is only meaningful against a *canonical*
form, so this module defines one:

* ``Decimal`` becomes its exact string. Never a float — the no-float law
  (Constitution §46.8) is worthless if the comparison format rounds.
* ``datetime`` becomes ISO-8601, normalised to UTC, so an equivalent instant
  in another offset cannot masquerade as a difference.
* Mapping keys are sorted; separators are compact. Dict insertion order is an
  implementation detail and must not leak into the comparison.
* Output is UTF-8 bytes, because the hash is over bytes, not over a str.

Two fields are deliberately excluded from canonical event form:

``created_at``
    A clock reading at write time, not a detection fact. It is fixed in the
    harness anyway, but including it would encode "when the harness ran" into
    the doctrine's fingerprint.

``event_key``
    A sha256 over (symbol, timeframe, event_type, event_at, algo_version) —
    fully derived from fields already compared, and not something a human
    labelling a dataset by hand could reasonably compute. Its real property,
    uniqueness, is asserted separately by the runner.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def canonicalise(value: Any) -> Any:
    """Recursively convert a value into JSON-safe canonical primitives."""

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime is not canonicalisable: {value!r}")
        return value.astimezone(UTC).isoformat()

    if isinstance(value, dict):
        return {str(key): canonicalise(item) for key, item in sorted(value.items())}

    if isinstance(value, list | tuple):
        return [canonicalise(item) for item in value]

    if isinstance(value, bool | int | str) or value is None:
        return value

    if isinstance(value, float):
        raise TypeError(
            "float found in detector output; the no-float law forbids it "
            f"at a comparison boundary (value={value!r})"
        )

    raise TypeError(f"value is not canonicalisable: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical UTF-8 encoding used for comparison and hashing."""

    return json.dumps(
        canonicalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_text(value: Any) -> str:
    """Human-readable canonical form, for assertion diffs."""

    return json.dumps(
        canonicalise(value),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )


def output_hash(value: Any) -> str:
    """Stable sha256 of the canonical form — the determinism fingerprint."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()
