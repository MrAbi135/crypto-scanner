"""Speed step 2: several engine processes, one owner per symbol (shards.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from scanner.application.detection.candle_close_consumer import CandleCloseConsumer
from scanner.application.detection.shards import Shard, shard_of
from scanner.application.ports.event_consumer import StreamEntry
from scanner.infrastructure.redis.event_consumer import _just_before
from scanner.shared import Timeframe

LIVE = [
    "BTCUSDT",
    "ETHUSDT",
    "ZECUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "SUIUSDT",
    "UNIUSDT",
    "ADAUSDT",
    "PUMPUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "LISTAUSDT",
    "LITEBUSDT",
]


def test_the_owner_of_a_symbol_is_pinned() -> None:
    """SHA-256, not Python's per-process salted `hash()`: two engines -- or
    one engine before and after a restart -- must agree on every owner."""
    assert sorted(s for s in LIVE if shard_of(s, 2) == 0) == [
        "BNBUSDT",
        "BTCUSDT",
        "ETHUSDT",
        "PUMPUSDT",
        "SUIUSDT",
        "XRPUSDT",
    ]


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_every_symbol_has_exactly_one_owner(count: int) -> None:
    shards = [Shard(index=i, count=count) for i in range(count)]

    for symbol in LIVE:
        assert sum(shard.owns(symbol) for shard in shards) == 1, symbol


def test_one_shard_is_the_engine_as_it_always_was() -> None:
    shard = Shard()

    assert shard.group == "engine", "a one-shard engine must keep its group and pending list"
    assert all(shard.owns(symbol) for symbol in LIVE)


def test_a_shard_group_names_its_split() -> None:
    """A 2-way group must never be resumed by a 3-way split, which owns a
    different set of symbols."""
    assert Shard(index=1, count=2).group == "engine-shard-1-of-2"
    assert Shard(index=1, count=3).group != Shard(index=1, count=2).group


@pytest.mark.parametrize(("index", "count"), [(2, 2), (-1, 2), (0, 0)])
def test_an_impossible_shard_is_refused(index: int, count: int) -> None:
    with pytest.raises(ValueError):
        Shard(index=index, count=count)


@pytest.mark.parametrize(
    ("entry_id", "before"),
    [("1700000000000-5", "1700000000000-4"), ("1700000000000-0", f"1699999999999-{2**64 - 1}")],
)
def test_a_seeded_group_starts_just_before_the_oldest_unfinished_close(entry_id, before) -> None:
    assert _just_before(entry_id) == before


class Recorder:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.ran: list[tuple[str, str]] = []
        self.fail = fail or set()

    async def run(self, symbol, timeframe, open_time) -> None:
        self.ran.append((symbol, timeframe.value))
        if symbol in self.fail:
            raise RuntimeError("boom")


def _entry(n: int, symbol: str, timeframe: Timeframe) -> StreamEntry:
    return StreamEntry(
        entry_id=f"1700000000000-{n}",
        fields={
            "event_type": "market.candle.closed",
            # The outbox envelope, as `parse_close` reads it.
            "payload": json.dumps(
                {
                    "payload": {
                        "symbol": symbol,
                        "timeframe": timeframe.value,
                        "open_time": datetime(2026, 10, 11, tzinfo=UTC).isoformat(),
                    }
                }
            ),
        },
    )


@pytest.mark.asyncio
async def test_a_shard_runs_its_own_closes_and_acks_the_others() -> None:
    """Every shard's group delivers every close; the owner runs it, the rest
    ack it unprocessed so it never sits in their pending list."""
    shard = Shard(index=0, count=2)
    mine, theirs = "BTCUSDT", "SOLUSDT"
    assert shard.owns(mine) and not shard.owns(theirs)

    runner = Recorder()
    entries = [
        _entry(1, mine, Timeframe.M5),
        _entry(2, theirs, Timeframe.M5),
        _entry(3, mine, Timeframe.H1),
    ]

    report, acked = await CandleCloseConsumer(runner, owns=shard.owns).consume(entries)

    assert runner.ran == [(mine, "H1"), (mine, "M5")], "HTF first, and only the owned symbol"
    assert set(acked) == {e.entry_id for e in entries}
    assert (report.processed, report.skipped, report.failed) == (2, 1, 0)


@pytest.mark.asyncio
async def test_an_owned_close_that_fails_stays_pending() -> None:
    shard = Shard(index=0, count=2)
    runner = Recorder(fail={"BTCUSDT"})

    report, acked = await CandleCloseConsumer(runner, owns=shard.owns).consume(
        [_entry(1, "BTCUSDT", Timeframe.M5), _entry(2, "SOLUSDT", Timeframe.M5)]
    )

    assert acked == ("1700000000000-2",), "only the other shard's close is acked"
    assert (report.processed, report.skipped, report.failed) == (0, 1, 1)
