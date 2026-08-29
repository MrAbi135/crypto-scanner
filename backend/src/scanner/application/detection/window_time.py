"""Rebase a persisted zone's frozen indices into today's candle window.

The zone upsert deliberately never revises `created_index`/`confirmed_index`
(zone identity must not drift), so both freeze at the offsets of whichever
500-candle window first detected the zone -- and live detections happen at
the window's tail, so the frozen values cluster at ~458-500 (host medians).
Every lifecycle then computed `age = candle_index - created_index` with a
current-window candle_index against that frozen offset, which meant a
tail-frozen zone could never see an age above a couple of candles: FVG's
200-candle expiry, OTE's 100 and OB's 250-without-a-test were unreachable
for them (0 of 372 BPRs ever expired; 21 of 909 FVGs), and resuming the walk
from `confirmed_index + 1` re-examined only the tail while silently skipping
whatever a restart had missed.

`created_at` is durable -- every zone type stamps it with its creation
candle's CLOSE -- so the true position in today's window is pure arithmetic,
and the confirmed offset rides along as a delta because both frozen indices
came from the SAME recording window.

A zone created before today's window gets a NEGATIVE created position. That
is not an error, it is the truth the frozen index was hiding: age arithmetic
against a negative origin yields the zone's real age, and the resume clamp
starts the walk at candle 0 -- the same shape the liquidity engine's pool
aging fix established.
"""

from __future__ import annotations

from collections.abc import Sequence

from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.domain.common import Candle
from scanner.shared import Timeframe


def rebased_indices(
    record: IctZoneRecord,
    candles: Sequence[Candle],
    timeframe: Timeframe,
) -> tuple[int, int]:
    """(created, confirmed) as positions in `candles`' coordinate system.

    Arithmetic over time rather than a scan, so a gap in the series shifts
    the estimate by the gap's width instead of failing -- an approximate true
    age still beats an exactly wrong frozen one, and §2.15's gap protocol
    keeps served windows contiguous in the ordinary case.
    """
    duration = timeframe.duration

    # created_at is the creation candle's close; its open is one duration
    # earlier. Integer floor division keeps sub-duration misalignment (a
    # rebuilt candle, a DST-free UTC series should have none) from drifting
    # the position by one.
    creation_open = record.created_at - duration
    created = int((creation_open - candles[0].open_time) / duration)

    confirmed = created + (record.confirmed_index - record.created_index)

    return created, confirmed
