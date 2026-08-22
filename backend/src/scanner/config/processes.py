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

    # Sprint S2 — T4 trade aggregates. Off by default: the aggTrade stream is
    # by far the highest-volume subscription Binance offers, and §6.5 (its only
    # consumer today) declares itself unread without it rather than breaking.
    ingest_trades: bool = False

    # Sprint S3b — the ingested context set, externalised.
    #
    # These lived as module-level tuples in `runtime/ingest.py` until S3b, which
    # is a Constitution §8.8 violation ("behavior may never depend on
    # hardcoded environment-specific values") -- staging and production could
    # not scan different universes without a code change.
    #
    # Comma-separated rather than JSON so an operator can write
    # SCANNER_INGEST_SYMBOLS=BTCUSDT,ETHUSDT without quoting a list.
    ingest_symbols: str = "BTCUSDT,ETHUSDT"

    # The ladder, bottom-up. HTF zone confirmation reads the timeframe below it
    # (SLS §5.9 via `_lower_timeframe`), so subscribing to H1 without M15 yields
    # zero confirmations, silently and forever. Whatever is listed here, list
    # its lower neighbour too.
    ingest_timeframes: str = "M5,M15,H1,H4"

    # Enough history for the SLS §1.9 detection gate (300) with headroom, so a
    # fresh deployment warms itself instead of waiting days for live closes.
    warmup_backfill_candles: int = Field(
        default=600,
        gt=300,
        le=5000,
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
