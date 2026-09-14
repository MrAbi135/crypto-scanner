"""Relative volume (SLS §2.11) — a shared derived measure, like ATR.

Lives in `common` because §6 (volume) and §7.1 (momentum participation) both
read it, and those two are siblings under the engine-acyclicity contract: they
may not import each other. It first shipped inside `domain/volume`, placed by
which engine happened to need it first rather than by what it is. §2.11 puts it
in Data Integrity & Derived Measures alongside ATR, which was already here.

Two baselines, not one, and the split is deliberate:

* **M5, M15, H1** — median of the same *time-of-day slot* over the prior 20
  days. Crypto volume has strong intraday seasonality (Asia/EU/US session
  waves), so a flat rolling baseline reads every session open as a spike.
* **H4, D1, W1** — plain rolling 20-candle median. Seasonality is negligible
  once a bar spans a third of a day.

Median, never mean: §2.11 rejects mean-based baselines outright because one
spike drags the baseline up and hides the next three.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from scanner.domain.common import Candle
from scanner.shared import Timeframe

BASELINE_DAYS = 20
BASELINE_CANDLES = 20

# Timeframes whose baseline is time-of-day aware (§2.11).
_SEASONAL: frozenset[Timeframe] = frozenset(
    {
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
    }
)


class RvolClass(str, Enum):
    """§6.1 bands. Boundaries are `P.volume.rvol_bands` = 1.5 / 3.0 / 5.0."""

    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    SPIKE = "SPIKE"
    ABNORMAL = "ABNORMAL"


_ELEVATED_AT = Decimal("1.5")
_SPIKE_AT = Decimal("3.0")
_ABNORMAL_AT = Decimal("5.0")


def uses_seasonal_baseline(timeframe: Timeframe) -> bool:
    return timeframe in _SEASONAL


def baseline_span(timeframe: Timeframe) -> timedelta:
    """How far before a candle §2.11's baseline reaches.

    Twenty days on the seasonal timeframes, twenty candles above them. A
    detection window is not that history (audit M8): 500 H1 candles leave the
    first 480 without twenty prior days, and 500 M15 or M5 candles leave all of
    them. Read from this far before a window, every candle in it has its
    baseline wherever the window starts.
    """
    if uses_seasonal_baseline(timeframe):
        return timedelta(days=BASELINE_DAYS)

    return timeframe.duration * BASELINE_CANDLES


def median(values: Sequence[Decimal]) -> Decimal | None:
    """Exact median. Returns None for an empty sample rather than guessing.

    The even case averages the two middles, which can land on a value no sample
    took -- correct for a baseline, and the reason this stays Decimal: the
    average of two Decimals is exact, the average of two floats is not.
    """
    if not values:
        return None

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2 == 1:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) / 2


def baseline_sample(
    candles: Sequence[Candle],
    index: int,
) -> tuple[Decimal, ...]:
    """The prior volumes this candle's RVOL is measured against.

    Strictly prior: index `i` never sees itself, or the baseline moves with the
    thing it is meant to measure and a spike partly explains itself away.
    """
    if index <= 0 or index >= len(candles):
        return ()

    subject = candles[index]

    if uses_seasonal_baseline(subject.timeframe):
        # Same slot in the day, prior days only. Comparing an 03:00 candle to
        # the 20 bars before it compares Asian night to Asian night's own lead-
        # in; comparing it to prior 03:00s is the comparison §2.11 asks for.
        slot = (subject.open_time.hour, subject.open_time.minute)

        same_slot = [
            candle.volume
            for candle in candles[:index]
            if (candle.open_time.hour, candle.open_time.minute) == slot
        ]

        return tuple(same_slot[-BASELINE_DAYS:])

    return tuple(candle.volume for candle in candles[index - BASELINE_CANDLES : index])


def relative_volume(
    candles: Sequence[Candle],
    index: int,
) -> Decimal | None:
    """RVOL for one candle, or None when the baseline is not yet complete.

    None rather than 1.0: an incomplete baseline is an unknown, and defaulting
    it to "normal" would let a cold context report NORMAL for every candle
    forever while looking perfectly healthy -- the failure shape this project
    keeps meeting.
    """
    sample = baseline_sample(candles, index)

    if uses_seasonal_baseline(candles[index].timeframe):
        if len(sample) < BASELINE_DAYS:
            return None
    elif len(sample) < BASELINE_CANDLES:
        return None

    base = median(sample)

    if base is None or base <= 0:
        # A zero baseline means the slot never traded. Dividing would be
        # infinity; §1.5.4 owns halted candles, so this is not ours to classify.
        return None

    return candles[index].volume / base


def relative_volumes(
    candles: Sequence[Candle],
    history: Sequence[tuple[datetime, Decimal]] = (),
) -> tuple[Decimal | None, ...]:
    """RVOL for every candle, the baseline read through `history` first.

    `history` is the ascending (open_time, volume) of the candles before
    `candles[0]` -- `baseline_span` of them. Each value is `relative_volume`
    over the history followed by the candles, so with no history the two agree
    exactly, and one pass replaces a rescan of the window per candle.
    """
    if not candles:
        return ()

    seasonal = uses_seasonal_baseline(candles[0].timeframe)
    needed = BASELINE_DAYS if seasonal else BASELINE_CANDLES

    prior = [volume for _, volume in history]
    slots: dict[tuple[int, int], list[Decimal]] = {}

    if seasonal:
        for open_time, volume in history:
            slots.setdefault((open_time.hour, open_time.minute), []).append(volume)

    values: list[Decimal | None] = []

    for candle in candles:
        if seasonal:
            earlier = slots.setdefault((candle.open_time.hour, candle.open_time.minute), [])
        else:
            earlier = prior

        sample = earlier[-needed:]
        earlier.append(candle.volume)

        base = median(sample) if len(sample) >= needed else None

        values.append(candle.volume / base if base is not None and base > 0 else None)

    return tuple(values)


def rvol_at(
    candles: Sequence[Candle],
    index: int,
    rvols: Sequence[Decimal | None] | None,
) -> Decimal | None:
    """RVOL at `index`, taken from a `relative_volumes` series when supplied.

    `None` keeps the window-only reading, for callers that hold no history.
    """
    if rvols is not None:
        return rvols[index]

    return relative_volume(candles, index)


def classify(rvol: Decimal | None) -> RvolClass | None:
    """§6.1 banding. Boundaries are inclusive-low, matching the spec's ranges."""
    if rvol is None:
        return None

    if rvol >= _ABNORMAL_AT:
        return RvolClass.ABNORMAL

    if rvol >= _SPIKE_AT:
        return RvolClass.SPIKE

    if rvol >= _ELEVATED_AT:
        return RvolClass.ELEVATED

    return RvolClass.NORMAL
