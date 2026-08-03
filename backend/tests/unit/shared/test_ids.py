"""ULID tests (S0.3 §2): parse∘generate identity, monotonic ordering, rejection."""

from __future__ import annotations

import pytest
from hypothesis import given

from scanner.shared import ValidationError
from scanner.shared.ids import as_ulid, monotonic_factory, new_ulid, parse_ulid
from tests.support import strategies as sg


def test_new_ulid_length_and_roundtrip() -> None:
    ulid = new_ulid(timestamp_ms=0)
    assert len(ulid) == 26
    ts, canonical = parse_ulid(ulid)
    assert ts == 0
    assert canonical == ulid


@pytest.mark.parametrize("bad_ts", [-1, 1 << 48])
def test_timestamp_range_enforced(bad_ts: int) -> None:
    with pytest.raises(ValidationError):
        new_ulid(timestamp_ms=bad_ts)


def test_parse_rejects_bad_length() -> None:
    with pytest.raises(ValidationError):
        parse_ulid("TOOSHORT")


def test_parse_rejects_invalid_time_char() -> None:
    with pytest.raises(ValidationError):
        parse_ulid("I" * 26)  # 'I' is excluded from Crockford base32


def test_parse_rejects_invalid_random_char() -> None:
    good = new_ulid(timestamp_ms=100)
    corrupted = good[:12] + "I" + good[13:]
    with pytest.raises(ValidationError):
        parse_ulid(corrupted)


def test_as_ulid_canonicalizes_case() -> None:
    ulid = new_ulid(timestamp_ms=5)
    assert as_ulid(ulid.lower()) == ulid


def test_monotonic_increases_within_same_ms() -> None:
    nxt = monotonic_factory()
    a, b, c = nxt(1000), nxt(1000), nxt(1000)
    assert a < b < c


def test_monotonic_orders_across_ms() -> None:
    nxt = monotonic_factory()
    earlier, later = nxt(1000), nxt(2000)
    assert later[:10] > earlier[:10]


def test_monotonic_rejects_bad_ts() -> None:
    nxt = monotonic_factory()
    with pytest.raises(ValidationError):
        nxt(-1)


@given(sg.epoch_millis())
def test_parse_generate_identity(ms: int) -> None:
    ulid = new_ulid(timestamp_ms=ms)
    ts, canonical = parse_ulid(ulid)
    assert ts == ms
    assert canonical == ulid
