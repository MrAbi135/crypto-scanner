"""Per-process settings schemas (TAD §14; S0.2 §8).

Each process constructs only its own class. Health ports default per process
but all read the single `SCANNER_HEALTH_PORT` var (compose overrides per
service).
"""

from __future__ import annotations

from pydantic import Field

from scanner.config.base import BaseProcessSettings


class ApiSettings(BaseProcessSettings):
    api_port: int = Field(default=8000, gt=0, le=65535)


class IngestSettings(BaseProcessSettings):
    health_port: int = Field(default=8001, gt=0, le=65535)

    # Binance provider config.
    binance_base_url: str = "https://api.binance.com"
    binance_weight_capacity: int = Field(
        default=1100,
        gt=0,
        le=6000,
    )

    # Sprint S2 — WebSocket configuration.
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    binance_ws_reconnect_delay_seconds: int = Field(
        default=5,
        gt=0,
        le=300,
    )


class EngineSettings(BaseProcessSettings):
    health_port: int = Field(default=8002, gt=0, le=65535)


class WorkerSettings(BaseProcessSettings):
    health_port: int = Field(default=8003, gt=0, le=65535)

    # Sprint S3 — daily universe/liquidity evaluation.
    binance_base_url: str = "https://api.binance.com"
    binance_weight_capacity: int = Field(
        default=1100,
        gt=0,
        le=6000,
    )


def load_ingest_settings() -> IngestSettings:
    """Backward-compatible constructor used by the ops CLI."""
    return IngestSettings()
