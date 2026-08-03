"""Per-process settings schemas (TAD §14; S0.2 §8).

Each process constructs only its own class. Health ports default per process
but all read the single `SCANNER_HEALTH_PORT` var (compose overrides per
service). The ingest process additionally carries the Binance provider config
that Sprint S1 introduced.
"""

from __future__ import annotations

from pydantic import Field

from scanner.config.base import BaseProcessSettings


class ApiSettings(BaseProcessSettings):
    api_port: int = Field(default=8000, gt=0, le=65535)


class IngestSettings(BaseProcessSettings):
    health_port: int = Field(default=8001, gt=0, le=65535)
    # Binance provider config (Sprint S1 — the ingest process owns it).
    binance_base_url: str = "https://api.binance.com"
    binance_weight_capacity: int = Field(default=1100, gt=0, le=6000)


class EngineSettings(BaseProcessSettings):
    health_port: int = Field(default=8002, gt=0, le=65535)


class WorkerSettings(BaseProcessSettings):
    health_port: int = Field(default=8003, gt=0, le=65535)


def load_ingest_settings() -> IngestSettings:
    """Backward-compatible constructor used by the ops CLI (Sprint S1)."""
    return IngestSettings()
