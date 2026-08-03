"""Config layer contracts (S0.2 §8)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError as PydanticValidationError

from scanner.config import ApiSettings, IngestSettings, get_settings, load_ingest_settings

_REQUIRED = {
    "SCANNER_ENV": "dev",
    "SCANNER_DB_DSN": "postgresql+asyncpg://u:p@h:5432/d",
    "SCANNER_REDIS_URL": "redis://h:6379/0",
}


@pytest.fixture
def scanner_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in list(os.environ):
        if key.startswith("SCANNER_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _set_required(mp: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED.items():
        mp.setenv(key, value)


def test_api_settings_typed(scanner_env: pytest.MonkeyPatch) -> None:
    _set_required(scanner_env)
    scanner_env.setenv("SCANNER_API_PORT", "8080")
    settings = get_settings("api")
    assert isinstance(settings, ApiSettings)
    assert settings.api_port == 8080
    assert settings.env == "dev"


def test_ingest_settings_carry_binance_defaults(scanner_env: pytest.MonkeyPatch) -> None:
    _set_required(scanner_env)
    settings = get_settings("ingest")
    assert isinstance(settings, IngestSettings)
    assert settings.health_port == 8001
    assert settings.binance_weight_capacity == 1100


def test_missing_db_dsn_names_the_field(scanner_env: pytest.MonkeyPatch) -> None:
    scanner_env.setenv("SCANNER_ENV", "dev")
    scanner_env.setenv("SCANNER_REDIS_URL", "redis://h:6379/0")
    with pytest.raises(PydanticValidationError) as excinfo:
        get_settings("ingest")
    assert "db_dsn" in str(excinfo.value)


def test_invalid_env_rejected(scanner_env: pytest.MonkeyPatch) -> None:
    _set_required(scanner_env)
    scanner_env.setenv("SCANNER_ENV", "production")  # not in dev|staging|prod
    with pytest.raises(PydanticValidationError):
        get_settings("api")


def test_unknown_process_rejected() -> None:
    with pytest.raises(ValueError, match="unknown process"):
        get_settings("nope")  # type: ignore[call-overload]


def test_frozen_settings_immutable(scanner_env: pytest.MonkeyPatch) -> None:
    _set_required(scanner_env)
    settings = load_ingest_settings()
    with pytest.raises(PydanticValidationError):
        settings.env = "prod"
