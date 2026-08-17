"""The success and error envelopes against API Spec §13 and §7."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.interfaces.api.envelope import (
    Freshness,
    Versions,
    encode,
    error,
    success,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
FRESH = Freshness(state="FRESH", observed_at=NOW)


def test_the_success_envelope_has_exactly_the_specified_keys() -> None:
    envelope = success({"x": 1}, generated_at=NOW, freshness=FRESH)

    assert set(envelope) == {"data", "meta"}
    assert set(envelope["meta"]) == {"generated_at", "freshness"}


def test_page_appears_only_when_supplied() -> None:
    """§13 marks `page` optional; an empty one on a single resource is noise."""
    without = success({"x": 1}, generated_at=NOW, freshness=FRESH)

    assert "page" not in without

    with_page = success(
        [1, 2],
        generated_at=NOW,
        freshness=FRESH,
        page={"count": 2, "has_more": False},
    )

    assert with_page["page"] == {"count": 2, "has_more": False}


def test_versions_appear_on_doctrine_derived_responses() -> None:
    envelope = success(
        [],
        generated_at=NOW,
        freshness=FRESH,
        versions=Versions(algo_version="s4-v1", param_set_version="p1"),
    )

    assert envelope["meta"]["versions"] == {
        "algo_version": "s4-v1",
        "param_set_version": "p1",
    }


def test_freshness_cannot_be_omitted() -> None:
    """Constitution §45.3: a degraded input may never render as fresh.

    Making the argument required rather than defaulted is the whole point --
    an endpoint that forgets to declare freshness must not compile into one
    that silently claims it.
    """
    with pytest.raises(TypeError):
        success([], generated_at=NOW)  # type: ignore[call-arg]


def test_delay_minutes_is_stated_or_absent_never_implied() -> None:
    plain = Freshness(state="FRESH").as_dict()

    assert "delay_minutes" not in plain

    delayed = Freshness(state="FRESH", delay_minutes=15).as_dict()

    assert delayed["delay_minutes"] == 15


def test_decimals_serialise_as_strings_not_floats() -> None:
    """API §5. A price that round-trips through IEEE-754 is a different price."""
    envelope = success(
        {"close": Decimal("0.000000123456789012345678")},
        generated_at=NOW,
        freshness=FRESH,
    )

    rendered = json.dumps(envelope)

    assert '"0.000000123456789012345678"' in rendered
    assert "1.23456789012345678e-07" not in rendered


def test_encoding_reaches_nested_structures() -> None:
    encoded = encode(
        {
            "rows": [
                {"price": Decimal("1.5"), "at": NOW},
                {"price": Decimal("2.0")},
            ]
        }
    )

    assert encoded["rows"][0]["price"] == "1.5"
    assert encoded["rows"][0]["at"] == NOW.isoformat()

    # Decimal("2.0") normalises to "2" -- canonical form, no trailing noise.
    assert encoded["rows"][1]["price"] == "2"


def test_the_error_envelope_has_exactly_the_specified_shape() -> None:
    envelope = error("NOT_FOUND", "no such symbol", correlation_id="01ABC")

    assert envelope == {
        "error": {
            "code": "NOT_FOUND",
            "message": "no such symbol",
            "correlation_id": "01ABC",
        }
    }


def test_validation_details_are_field_precise() -> None:
    envelope = error(
        "VALIDATION_FAILED",
        "bad timeframe",
        correlation_id="01ABC",
        details=[{"field": "timeframe", "code": "INVALID", "message": "unknown"}],
    )

    assert envelope["error"]["details"] == [
        {"field": "timeframe", "code": "INVALID", "message": "unknown"}
    ]


def test_retry_after_is_present_only_when_given() -> None:
    """§7 requires it on 429 — and forbids it being invented elsewhere."""
    assert "retry_after" not in error("INTERNAL", "x", correlation_id="1")["error"]

    limited = error("RATE_LIMITED", "slow down", correlation_id="1", retry_after=30)

    assert limited["error"]["retry_after"] == 30
