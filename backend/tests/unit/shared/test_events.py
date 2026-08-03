"""EventEnvelope tests (S0.3 §2): validation, immutability, canonical serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.shared import DomainInvariantError
from scanner.shared.events import EventEnvelope
from scanner.shared.ids import new_ulid
from tests.support.asserts import assert_frozen

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _envelope(**overrides: object) -> EventEnvelope:
    fields: dict[str, object] = {
        "event_type": "market.candle.closed",
        "event_id": new_ulid(timestamp_ms=1),
        "occurred_at": _TS,
        "payload": {},
    }
    fields.update(overrides)
    return EventEnvelope(**fields)  # type: ignore[arg-type]


def test_valid_envelope_defaults() -> None:
    envelope = _envelope()
    assert envelope.schema_version == 1
    assert envelope.flow_id is None


@pytest.mark.parametrize("bad_type", ["notnamespaced", "has space", ""])
def test_rejects_bad_event_type(bad_type: str) -> None:
    with pytest.raises(DomainInvariantError):
        _envelope(event_type=bad_type)


def test_rejects_naive_datetime() -> None:
    with pytest.raises(DomainInvariantError):
        _envelope(occurred_at=datetime(2024, 1, 1))


def test_rejects_bad_schema_version() -> None:
    with pytest.raises(DomainInvariantError):
        _envelope(schema_version=0)


def test_is_immutable() -> None:
    assert_frozen(_envelope(), "event_type", "x.y.z")


def test_decimal_payload_serializes_to_canonical_string() -> None:
    data = json.loads(_envelope(payload={"price": Decimal("100.50"), "n": 3}).to_json())
    assert data["payload"]["price"] == "100.5"
    assert data["payload"]["n"] == 3


def test_datetime_payload_serializes_isoformat() -> None:
    data = json.loads(_envelope(payload={"at": _TS}).to_json())
    assert data["payload"]["at"] == _TS.isoformat()


def test_unserializable_payload_raises() -> None:
    with pytest.raises(TypeError):
        _envelope(payload={"bad": object()}).to_json()


def test_to_json_has_sorted_keys() -> None:
    body = _envelope().to_json()
    assert body.index('"event_id"') < body.index('"event_type"') < body.index('"payload"')
