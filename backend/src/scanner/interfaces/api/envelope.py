"""The one success envelope and the one error envelope (API Spec §13, §7).

Every response in the platform passes through here. Written once, in the shape
the frozen spec defines, so no endpoint has to remember it.

Two rules from the spec that are easy to get wrong and expensive to get wrong:

* **`meta.freshness` on every market- or detection-derived response.** A
  degraded input may never render as fresh (Constitution §45.3). Absent
  freshness is not "assume fine" -- it is a missing declaration, so the helper
  requires it rather than defaulting it.
* **Numbers as strings** (§5). Decimal must never reach JSON as a float; a
  price that round-trips through IEEE-754 is a different price.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from scanner.shared.decimal_math import to_canonical_str

# Param sets are an S8 deliverable (SLS §8, the weighted factor stack). No
# scoring reads a versioned parameter yet, so there is no version to report.
#
# Reported as this sentinel rather than "1.0.0" or "default", either of which
# would read as a real version and quietly become a false provenance claim on
# every doctrine response. It is greppable, and it disappears the moment S8
# gives it something true to say.
NO_PARAM_SET = "none:pre-s8"


@dataclass(frozen=True, slots=True)
class Versions:
    """`meta.versions` -- required on doctrine-derived responses (SLS §15.2)."""

    algo_version: str
    param_set_version: str = NO_PARAM_SET

    def as_dict(self) -> dict[str, str]:
        return {
            "algo_version": self.algo_version,
            "param_set_version": self.param_set_version,
        }


@dataclass(frozen=True, slots=True)
class Freshness:
    """`meta.freshness` -- per-source staleness (SLS §2.12)."""

    state: str
    observed_at: datetime | None = None
    delay_minutes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state}

        if self.observed_at is not None:
            payload["observed_at"] = self.observed_at.isoformat()

        # Only on delayed tiers, and then always explicit (§13). A silent delay
        # is the same lie as a stale value rendered as live.
        if self.delay_minutes is not None:
            payload["delay_minutes"] = self.delay_minutes

        return payload


def success(
    data: Any,
    *,
    generated_at: datetime,
    freshness: Freshness,
    versions: Versions | None = None,
    page: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the §13 success envelope.

    `freshness` is a required argument rather than an optional one: every
    endpoint this module serves is market- or detection-derived, and making it
    optional would let a caller omit the one field that stops stale data being
    presented as live.
    """
    meta: dict[str, Any] = {
        "generated_at": generated_at.isoformat(),
        "freshness": freshness.as_dict(),
    }

    if versions is not None:
        meta["versions"] = versions.as_dict()

    envelope: dict[str, Any] = {
        "data": encode(data),
        "meta": meta,
    }

    if page is not None:
        envelope["page"] = dict(page)

    return envelope


def error(
    code: str,
    message: str,
    *,
    correlation_id: str,
    details: Sequence[Mapping[str, str]] | None = None,
    retry_after: int | None = None,
) -> dict[str, Any]:
    """Build the §7 error envelope.

    `message` must be user-safe. Never pass an exception string through here:
    §7 forbids stack traces and SQL reaching a client, and the correlation id
    is what ties the safe message to the unsafe detail in the logs.
    """
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
    }

    if details:
        payload["details"] = [dict(detail) for detail in details]

    if retry_after is not None:
        payload["retry_after"] = retry_after

    return {"error": payload}


def encode(value: Any) -> Any:
    """Recursively render a payload as JSON-safe values.

    Decimal becomes its canonical string, never a float (§5). datetime becomes
    ISO-8601. Anything else is passed through for the JSON encoder to reject
    loudly if it cannot handle it -- a silent repr would ship nonsense.
    """
    if isinstance(value, Decimal):
        return to_canonical_str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Mapping):
        return {str(key): encode(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]

    return value
