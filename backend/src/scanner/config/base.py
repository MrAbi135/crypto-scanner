"""Base per-process settings (TAD §14; S0.2 §8).

Every process's settings inherit these infrastructure fields. `frozen=True`
makes the object immutable post-boot; a missing/invalid variable raises
pydantic `ValidationError` at construction, which the composition root lets
abort the boot with a field-precise message.

`extra="ignore"` is retained (not "forbid") because all processes share one env
file, so a per-process "forbid" would reject sibling processes' variables
(S1 deviation #1 → ADR-003).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseProcessSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCANNER_", frozen=True, extra="ignore")

    env: str = Field(pattern="^(dev|staging|prod)$")
    log_level: str = "INFO"
    db_dsn: str
    redis_url: str
    sentry_dsn: str = ""
    release: str = "local"  # git SHA injected at image build (SCANNER_RELEASE)
