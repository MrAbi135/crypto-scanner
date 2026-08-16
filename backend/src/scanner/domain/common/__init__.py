"""Shared domain value objects (TAD §30)."""

from scanner.domain.common.atr import (
    ATR_PERIOD,
    DERIVED_DP,
    quantise_derived,
    true_range,
    wilder_atr,
)
from scanner.domain.common.candle import Candle, CandleSource
from scanner.domain.common.symbol import Symbol, SymbolStatus
from scanner.domain.common.warmup import (
    DETECTION_MIN_CANDLES,
    LISTING_MIN_DAYS,
    VOLUME_MOMENTUM_MIN_CANDLES,
    WarmupCapability,
    detection_is_warm,
    is_warm,
    minimum_candles,
)

__all__ = [
    "ATR_PERIOD",
    "DERIVED_DP",
    "DETECTION_MIN_CANDLES",
    "LISTING_MIN_DAYS",
    "VOLUME_MOMENTUM_MIN_CANDLES",
    "Candle",
    "CandleSource",
    "Symbol",
    "SymbolStatus",
    "WarmupCapability",
    "detection_is_warm",
    "is_warm",
    "minimum_candles",
    "quantise_derived",
    "true_range",
    "wilder_atr",
]
