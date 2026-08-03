"""Logging foundation behaviour (S0.2 §12)."""

from __future__ import annotations

import json

import pytest
import structlog

from scanner.infrastructure.observability.logging import (
    bind_correlation_id,
    configure_logging,
    scrub_event,
)


def _last_json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    text = (captured.out or captured.err).strip()
    assert text, "expected a log line on stdout/stderr"
    return json.loads(text.splitlines()[-1])  # type: ignore[no-any-return]


def test_emits_json_with_service_and_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "test-svc")
    structlog.get_logger().info("hello.world", foo=1)
    record = _last_json_line(capsys)
    assert record["service"] == "test-svc"
    assert record["event"] == "hello.world"
    assert record["level"] == "info"
    assert record["foo"] == 1


def test_secrets_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "test-svc")
    structlog.get_logger().info("auth.attempt", password="hunter2", api_token="abc")
    record = _last_json_line(capsys)
    assert record["password"] == "***redacted***"
    assert record["api_token"] == "***redacted***"


def test_correlation_id_is_bound(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "test-svc")
    bind_correlation_id("cid-123")
    structlog.get_logger().info("with.cid")
    record = _last_json_line(capsys)
    assert record["correlation_id"] == "cid-123"


def test_release_is_bound_when_provided(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "test-svc", "rel-abc")
    structlog.get_logger().info("with.release")
    assert _last_json_line(capsys)["release"] == "rel-abc"


def test_scrub_event_redacts_nested_secrets() -> None:
    event = {"extra": {"password": "hunter2", "safe": 1}, "crumbs": [{"api_token": "abc"}]}
    scrubbed = scrub_event(event, None)
    assert scrubbed["extra"]["password"] == "***redacted***"
    assert scrubbed["extra"]["safe"] == 1
    assert scrubbed["crumbs"][0]["api_token"] == "***redacted***"
