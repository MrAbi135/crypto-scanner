"""Shared domain value objects (TAD §30)."""

from scanner.domain.common.atr import (
    ATR_PERIOD,
    DERIVED_DP,
    quantise_derived,
    true_range,
    wilder_atr,
)
from scanner.domain.common.candle import Candle, CandleSource
from scanner.domain.common.rvol import (
    BASELINE_CANDLES,
    BASELINE_DAYS,
    RvolClass,
    baseline_sample,
    classify,
    median,
    relative_volume,
    uses_seasonal_baseline,
)
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
    "BASELINE_CANDLES",
    "BASELINE_DAYS",
    "DERIVED_DP",
    "DETECTION_MIN_CANDLES",
    "LISTING_MIN_DAYS",
    "VOLUME_MOMENTUM_MIN_CANDLES",
    "Candle",
    "CandleSource",
    "RvolClass",
    "Symbol",
    "SymbolStatus",
    "WarmupCapability",
    "baseline_sample",
    "classify",
    "detection_is_warm",
    "is_warm",
    "median",
    "minimum_candles",
    "quantise_derived",
    "relative_volume",
    "true_range",
    "uses_seasonal_baseline",
    "wilder_atr",
]
