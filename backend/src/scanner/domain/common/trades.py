"""Per-minute trade aggregates from aggTrade prints (SLS §2.2, DDD T4).

SLS §2 settles the retention question for us: "raw individual ticks are **not**
retained beyond aggregation in v1 (cost/benefit: aggTrades preserve taker side
and size distribution, which is all current doctrine consumes)". So the minute
bucket is the record, and whatever the doctrine needs from the distribution has
to survive into it -- nothing downstream can go back to the prints.

Two consumers, and between them they fix the field list:

* §6.5's institutional-volume signature reads "90th-percentile trade size on
  the candle >= 2x its trailing 20-candle median of the same percentile";
* §6.6's third fake-volume test is "coefficient of variation of trade sizes
  < 0.2", and a coefficient of variation is stddev over mean.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from scanner.domain.common.atr import quantise_derived

# §6.5 names this percentile explicitly; it is not a tunable.
INSTITUTIONAL_PERCENTILE = Decimal("0.90")


@dataclass(frozen=True, slots=True)
class TradePrint:
    """One aggTrade print, in the only three terms the doctrine consumes."""

    at: datetime
    size: Decimal
    taker_is_buyer: bool

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("trade size must be positive")


@dataclass(frozen=True, slots=True)
class TradeAggregate:
    """One minute of prints for one symbol."""

    symbol: str
    minute: datetime
    taker_buy_volume: Decimal
    taker_sell_volume: Decimal
    trade_count: int
    mean_trade_size: Decimal
    stddev_trade_size: Decimal
    p90_trade_size: Decimal
    max_trade_size: Decimal


def minute_of(at: datetime) -> datetime:
    """The bucket a print belongs to."""
    return at.replace(second=0, microsecond=0)


def percentile(sizes: Sequence[Decimal], fraction: Decimal) -> Decimal:
    """Nearest-rank percentile over a sorted-ascending sequence.

    Nearest-rank rather than interpolated, and stated here because §6.5
    compares a candle's percentile against a median of the same percentile
    over twenty candles: any method works for that comparison provided it is
    the same one every time, and an interpolated percentile invents a trade
    size that nobody printed.
    """
    if not sizes:
        raise ValueError("percentile of an empty sequence is undefined")

    rank = int((fraction * Decimal(len(sizes))).to_integral_value(rounding=ROUND_CEILING))

    return sizes[min(max(rank, 1), len(sizes)) - 1]


def aggregate_minute(
    symbol: str,
    minute: datetime,
    prints: Sequence[TradePrint],
) -> TradeAggregate | None:
    """Fold one minute's prints into the row DDD T4 stores.

    None for an empty minute rather than a zero row: a bucket exists because
    prints arrived, and a row claiming a trade count of zero would divide by
    itself in every mean downstream.
    """
    if not prints:
        return None

    sizes = sorted(item.size for item in prints)
    count = len(sizes)

    total = sum(sizes, Decimal(0))
    mean = total / Decimal(count)

    # Population, not sample: this is the whole minute, not a draw from it.
    variance = sum(((size - mean) ** 2 for size in sizes), Decimal(0)) / Decimal(count)

    buy = sum((item.size for item in prints if item.taker_is_buyer), Decimal(0))

    return TradeAggregate(
        symbol=symbol,
        minute=minute,
        taker_buy_volume=quantise_derived(buy),
        taker_sell_volume=quantise_derived(total - buy),
        trade_count=count,
        mean_trade_size=quantise_derived(mean),
        stddev_trade_size=quantise_derived(variance.sqrt()),
        p90_trade_size=quantise_derived(percentile(sizes, INSTITUTIONAL_PERCENTILE)),
        max_trade_size=quantise_derived(sizes[-1]),
    )


def aggregate_prints(symbol: str, prints: Iterable[TradePrint]) -> tuple[TradeAggregate, ...]:
    """Every complete minute in `prints`, oldest first.

    The caller decides which minutes are finished. A live stream must not fold
    the minute currently in progress, or the row it writes is a partial
    distribution that a later print cannot correct -- the aggregate is the
    record, and there are no prints to recompute it from.
    """
    buckets: dict[datetime, list[TradePrint]] = {}

    for item in prints:
        buckets.setdefault(minute_of(item.at), []).append(item)

    aggregates = [
        aggregate_minute(symbol, minute, items) for minute, items in sorted(buckets.items())
    ]

    return tuple(item for item in aggregates if item is not None)
