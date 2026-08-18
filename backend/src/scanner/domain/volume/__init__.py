"""Volume engine (SLS §6).

Volume is confirmation evidence, never a standalone signal, and in crypto it is
the most manipulable dimension in the dataset -- so this engine is built
defensively and every reading carries its own evidence.

**What is here:** RVOL and its banding (§2.11, §6.1), volume spike (§6.2), and
expansion / contraction (§6.3). All computable from stored candles alone.

**What is not, and why:** §6.5 institutional volume signature needs a
per-candle 90th-percentile trade size, which requires aggTrade aggregates.
Those are an S2 deliverable ("T4 trade aggregates, 1m buckets from aggTrade")
that was never built -- the Binance adapter fetches klines, depth, ticker and
exchangeInfo only. §6.4 and §6.6 are partially reachable: their trade-count and
delta tests work today, their order-book tests have only daily depth from the
universe collector rather than the per-candle snapshot the spec asks for.

Building those now would add detectors nothing can call, which is the mistake
recorded in docs/evidence/S5/CHECKLIST.md. They wait for the ingest work.
"""

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
from scanner.domain.volume.detectors import (
    CONTRACTION_WINDOW,
    CONVICTION_DELTA,
    SPIKE_FLOOR_QUOTE,
    VolumeSpike,
    delta_pct,
    detect_contraction,
    detect_expansion,
    detect_volume_spike,
)
from scanner.domain.volume.factor import (
    VolumeContribution,
    VolumeFactor,
    VolumeFactorEvidence,
    volume_factor_score,
)

__all__ = [
    "BASELINE_CANDLES",
    "BASELINE_DAYS",
    "CONTRACTION_WINDOW",
    "CONVICTION_DELTA",
    "SPIKE_FLOOR_QUOTE",
    "RvolClass",
    "VolumeContribution",
    "VolumeFactor",
    "VolumeFactorEvidence",
    "VolumeSpike",
    "baseline_sample",
    "classify",
    "delta_pct",
    "detect_contraction",
    "detect_expansion",
    "detect_volume_spike",
    "median",
    "relative_volume",
    "uses_seasonal_baseline",
    "volume_factor_score",
]
