"""UTC time and timeframe boundary arithmetic — the single home of candle
boundary math (TAD §15). No wall-clock access: time is always an input.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from enum import Enum

from scanner.shared.constants import UTC
from scanner.shared.errors import ValidationError

_MS = 1000


class Timeframe(str, Enum):
    """The scanned timeframe set (SLS §0.2). M1 is deliberately absent."""

    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"

    @property
    def minutes(self) -> int:
        return _TF_MINUTES[self]

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    @classmethod
    def parse(cls, raw: str) -> Timeframe:
        try:
            return cls(raw.upper())
        except ValueError as exc:
            raise ValidationError(
                f"unknown timeframe: {raw!r}", details={"allowed": [t.value for t in cls]}
            ) from exc


_TF_MINUTES: dict[Timeframe, int] = {
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
}

# Monday 1970-01-05 00:00 UTC — the epoch anchor for ISO-week (W1) boundaries.
_W1_ANCHOR_MS = 4 * 86_400 * _MS


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() != timedelta(0):
        raise ValidationError(f"timestamp must be timezone-aware UTC, got {ts!r}")
    return ts


def utc_ms(ts: datetime) -> int:
    """Milliseconds since epoch (exchange-native unit)."""
    return int(_require_utc(ts).timestamp() * _MS)


def utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / _MS, tz=UTC)


def floor_to_boundary(ts: datetime, tf: Timeframe) -> datetime:
    """Largest candle open time ≤ ts. W1 floors to ISO Monday 00:00 UTC."""
    ms = utc_ms(ts)
    step = tf.minutes * 60 * _MS
    anchor = _W1_ANCHOR_MS if tf is Timeframe.W1 else 0
    return utc_from_ms(anchor + ((ms - anchor) // step) * step)


def next_boundary(ts: datetime, tf: Timeframe) -> datetime:
    floored = floor_to_boundary(ts, tf)
    return floored + tf.duration if floored <= ts else floored


def is_boundary(ts: datetime, tf: Timeframe) -> bool:
    return floor_to_boundary(ts, tf) == _require_utc(ts)


def span_boundaries(start: datetime, end: datetime, tf: Timeframe) -> Iterator[datetime]:
    """All candle open times in [start, end), aligned. start must be a boundary."""
    if not is_boundary(start, tf):
        raise ValidationError(f"span start {start.isoformat()} is not a {tf.value} boundary")
    current = start
    while current < end:
        yield current
        current = current + tf.duration
