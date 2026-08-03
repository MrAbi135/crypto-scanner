"""ULID identifiers: sortable, collision-safe, process-independent (TAD §15)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from scanner.shared.errors import ValidationError
from scanner.shared.types import Ulid

_ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_DECODE = {c: i for i, c in enumerate(_ENCODING)}
_TIME_LEN = 10
_RAND_LEN = 16


def new_ulid(*, timestamp_ms: int | None = None) -> Ulid:
    """Generate a ULID. Timestamp injectable for deterministic tests."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if ts < 0 or ts >= 1 << 48:
        raise ValidationError(f"ulid timestamp out of range: {ts}")
    time_part = _encode(ts, _TIME_LEN)
    rand = int.from_bytes(os.urandom(10), "big")
    return Ulid(time_part + _encode(rand, _RAND_LEN))


def as_ulid(value: str) -> Ulid:
    """Validate and canonicalize an existing string as a ULID."""
    _, canonical = parse_ulid(value)
    return Ulid(canonical)


def monotonic_factory() -> Callable[[int], Ulid]:
    """Return a generator yielding strictly increasing ULIDs.

    Within the same millisecond the random component is incremented, so
    lexicographic order matches call order (ULID monotonicity, TAD §15).
    """
    state = {"ts": -1, "rand": 0}

    def _next(timestamp_ms: int) -> Ulid:
        if timestamp_ms < 0 or timestamp_ms >= 1 << 48:
            raise ValidationError(f"ulid timestamp out of range: {timestamp_ms}")
        if timestamp_ms == state["ts"]:
            state["rand"] += 1
        else:
            state["ts"] = timestamp_ms
            state["rand"] = int.from_bytes(os.urandom(10), "big")
        return Ulid(_encode(timestamp_ms, _TIME_LEN) + _encode(state["rand"], _RAND_LEN))

    return _next


def parse_ulid(raw: str) -> tuple[int, str]:
    """Validate a ULID; returns (timestamp_ms, canonical_form)."""
    if len(raw) != _TIME_LEN + _RAND_LEN:
        raise ValidationError(f"invalid ulid length: {raw!r}")
    canonical = raw.upper()
    try:
        ts = _decode(canonical[:_TIME_LEN])
    except KeyError as exc:
        raise ValidationError(f"invalid ulid character in {raw!r}") from exc
    for char in canonical[_TIME_LEN:]:
        if char not in _DECODE:
            raise ValidationError(f"invalid ulid character in {raw!r}")
    return ts, canonical


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ENCODING[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _decode(text: str) -> int:
    value = 0
    for char in text:
        value = (value << 5) | _DECODE[char]
    return value
