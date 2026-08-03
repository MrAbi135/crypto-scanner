"""The SLS §2.15 validation battery — series-level checks.

Intrinsic single-candle sanity lives in the Candle constructor (a Candle
that exists is shape-sane). This module validates *batches*: ordering,
duplicates, alignment span, continuity, and the cross-timeframe
aggregation check. Pure functions — the battery never touches I/O, so it
is equally the backfill gatekeeper (S1) and the stream gatekeeper (S2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from scanner.domain.common import Candle

# Aggregation tolerance: exchange-reported native candles and our aggregate
# of finer candles may differ by dust on volume (rounding at source).
# Prices must match exactly; volumes within 1 part in 10^6 (SLS §2.15).
_VOLUME_REL_TOLERANCE = Decimal("0.000001")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    check: str  # "ordering" | "duplicate" | "continuity_gap" | "aggregation_mismatch"
    message: str
    open_time: datetime | None = None
    gap_candles: int = 0


@dataclass(frozen=True, slots=True)
class BatchValidationResult:
    ok: bool
    findings: tuple[ValidationFinding, ...] = field(default=())

    @property
    def gaps(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.check == "continuity_gap")


def validate_batch(
    candles: Sequence[Candle],
    *,
    expected_prev_open: datetime | None = None,
) -> BatchValidationResult:
    """Validate an ascending batch of one (symbol, timeframe) series.

    expected_prev_open: open_time of the last candle already persisted —
    continuity is checked across the persistence boundary, not just inside
    the batch (a gap hiding at a chunk seam is still a gap).
    """
    findings: list[ValidationFinding] = []
    if not candles:
        return BatchValidationResult(ok=True)

    tf = candles[0].timeframe
    symbol = candles[0].symbol
    step = tf.duration

    for candle in candles:
        if candle.timeframe is not tf or candle.symbol != symbol:
            findings.append(
                ValidationFinding(
                    "ordering",
                    f"mixed series in batch: {candle.symbol}/{candle.timeframe.value} "
                    f"inside {symbol}/{tf.value}",
                    candle.open_time,
                )
            )
            return BatchValidationResult(ok=False, findings=tuple(findings))

    previous: Candle | None = None
    for candle in candles:
        if previous is not None:
            if candle.open_time <= previous.open_time:
                kind = "duplicate" if candle.open_time == previous.open_time else "ordering"
                findings.append(
                    ValidationFinding(
                        kind,
                        f"{symbol} {tf.value}: {kind} at {candle.open_time.isoformat()}",
                        candle.open_time,
                    )
                )
                continue
            delta = candle.open_time - previous.open_time
            if delta != step:
                missing = int(delta / step) - 1
                findings.append(
                    ValidationFinding(
                        "continuity_gap",
                        f"{symbol} {tf.value}: {missing} candle(s) missing after "
                        f"{previous.open_time.isoformat()}",
                        previous.open_time,
                        gap_candles=missing,
                    )
                )
        previous = candle

    if expected_prev_open is not None:
        first = candles[0]
        if first.open_time <= expected_prev_open:
            findings.append(
                ValidationFinding(
                    "ordering",
                    f"{symbol} {tf.value}: batch begins at/before persisted tail "
                    f"{expected_prev_open.isoformat()}",
                    first.open_time,
                )
            )
        else:
            delta = first.open_time - expected_prev_open
            if delta != step:
                missing = int(delta / step) - 1
                findings.append(
                    ValidationFinding(
                        "continuity_gap",
                        f"{symbol} {tf.value}: {missing} candle(s) missing between persisted tail "
                        f"{expected_prev_open.isoformat()} and batch start",
                        expected_prev_open,
                        gap_candles=missing,
                    )
                )

    # Gaps are recordable facts, not batch-fatal (SLS §2.16 — the honest ledger
    # handles them); ordering/duplicate corruption IS fatal to the batch.
    fatal = any(f.check in ("ordering", "duplicate") for f in findings)
    return BatchValidationResult(ok=not fatal, findings=tuple(findings))


def verify_aggregation(native: Candle, finer: Sequence[Candle]) -> ValidationFinding | None:
    """Cross-TF check (SLS §2.15): a native candle must equal the aggregate
    of its constituent finer candles. Returns a finding on mismatch.
    """
    if not finer:
        return ValidationFinding(
            "aggregation_mismatch",
            f"{native.symbol} {native.timeframe.value} {native.open_time.isoformat()}: "
            "no finer candles supplied for aggregation check",
            native.open_time,
        )
    expected_count = int(native.timeframe.duration / finer[0].timeframe.duration)
    span_ok = (
        len(finer) == expected_count
        and finer[0].open_time == native.open_time
        and finer[-1].close_time == native.close_time
    )
    if not span_ok:
        return ValidationFinding(
            "aggregation_mismatch",
            f"{native.symbol} {native.timeframe.value} {native.open_time.isoformat()}: "
            f"finer span mismatch (got {len(finer)}, want {expected_count} aligned)",
            native.open_time,
        )

    agg_open = finer[0].open
    agg_close = finer[-1].close
    agg_high = max(c.high for c in finer)
    agg_low = min(c.low for c in finer)
    agg_volume = sum((c.volume for c in finer), _ZERO)

    prices_ok = (
        agg_open == native.open
        and agg_close == native.close
        and agg_high == native.high
        and agg_low == native.low
    )
    if native.volume == _ZERO:
        volume_ok = agg_volume == _ZERO
    else:
        volume_ok = abs(agg_volume - native.volume) / native.volume <= _VOLUME_REL_TOLERANCE

    if prices_ok and volume_ok:
        return None
    return ValidationFinding(
        "aggregation_mismatch",
        f"{native.symbol} {native.timeframe.value} {native.open_time.isoformat()}: "
        f"aggregate disagrees with native (prices_ok={prices_ok}, volume_ok={volume_ok})",
        native.open_time,
    )
