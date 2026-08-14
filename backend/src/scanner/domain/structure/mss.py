"""Market Structure Shift evidence rules (SLS §3.6)."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.domain.structure.breaks import BreakDirection

_MSS_FOLLOWTHROUGH_MAX = 5
_MSS_INVALIDATION_MAX = 10


@dataclass(frozen=True, slots=True)
class MssEvidence:
    """Evidence chain required to publish an MSS."""

    direction: BreakDirection
    has_choch: bool
    has_displacement: bool
    has_external_sweep: bool = False
    has_failure_swing: bool = False
    followthrough_candles: int | None = None
    spans_degraded_data: bool = False


@dataclass(frozen=True, slots=True)
class MssDecision:
    """Deterministic MSS evaluation result."""

    confirmed: bool
    reason: str


def evaluate_mss(
    evidence: MssEvidence,
) -> MssDecision:
    """Evaluate the complete three-condition MSS doctrine."""

    if evidence.spans_degraded_data:
        return MssDecision(
            confirmed=False,
            reason="degraded_data",
        )

    if not evidence.has_choch:
        return MssDecision(
            confirmed=False,
            reason="missing_choch",
        )

    if not evidence.has_displacement:
        return MssDecision(
            confirmed=False,
            reason="missing_displacement",
        )

    if not (evidence.has_external_sweep or evidence.has_failure_swing):
        return MssDecision(
            confirmed=False,
            reason="missing_origin_evidence",
        )

    if evidence.followthrough_candles is None:
        return MssDecision(
            confirmed=False,
            reason="missing_followthrough",
        )

    if evidence.followthrough_candles < 1:
        raise ValueError("followthrough_candles must be positive")

    if evidence.followthrough_candles > _MSS_FOLLOWTHROUGH_MAX:
        return MssDecision(
            confirmed=False,
            reason="followthrough_expired",
        )

    return MssDecision(
        confirmed=True,
        reason="confirmed",
    )


def mss_is_low_quality(
    *,
    closes_back_beyond_pre_mss_extreme: bool,
    candles_since_confirmation: int,
) -> bool:
    """Apply the SLS post-MSS 10-candle quality invalidation rule."""

    if candles_since_confirmation < 0:
        raise ValueError("candles_since_confirmation must be non-negative")

    return (
        closes_back_beyond_pre_mss_extreme and candles_since_confirmation <= _MSS_INVALIDATION_MAX
    )
