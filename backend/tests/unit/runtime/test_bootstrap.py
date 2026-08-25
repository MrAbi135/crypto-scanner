"""Bootstrap + Sentry-gating tests (S0.3 §8.1)."""

from __future__ import annotations

import pytest

from scanner.config.processes import ApiSettings
from scanner.runtime.wiring import bootstrap


def _settings(**overrides: object) -> ApiSettings:
    fields: dict[str, object] = {
        "env": "dev",
        "db_dsn": "postgresql+asyncpg://u:p@h:5432/d",
        "redis_url": "redis://h:6379/0",
        # S10: ApiSettings has no default for this, deliberately.
        "access_token_secret": "a-test-signing-secret-of-sufficient-length",
    }
    fields.update(overrides)
    return ApiSettings(**fields)  # type: ignore[arg-type]


def test_sentry_disabled_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(bootstrap.sentry_sdk, "init", lambda **_: calls.__setitem__("n", 1))
    bootstrap.init_sentry(_settings())
    assert calls["n"] == 0


def test_sentry_enabled_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(bootstrap.sentry_sdk, "init", lambda **kw: captured.update(kw))
    bootstrap.init_sentry(_settings(sentry_dsn="https://k@example.test/1", release="abc123"))
    assert captured["environment"] == "dev"
    assert captured["release"] == "abc123"
    assert captured["traces_sample_rate"] == 0.0


def test_bootstrap_configures_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap.sentry_sdk, "init", lambda **_: None)
    bootstrap.bootstrap(_settings(), "api")
