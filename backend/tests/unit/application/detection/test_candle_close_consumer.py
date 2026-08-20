"""Candle-close consumer: parsing, ordering, and failure isolation (S4b)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from scanner.application.detection.candle_close_consumer import (
    CandleCloseConsumer,
    order_for_detection,
    parse_close,
)
from scanner.application.ports.event_consumer import StreamEntry
from scanner.shared import Timeframe
from scanner.shared.errors import DomainInvariantError

BASE = datetime(2026, 8, 17, tzinfo=UTC)


def entry(
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.H1,
    *,
    index: int = 0,
    entry_id: str | None = None,
) -> StreamEntry:
    open_time = BASE + timeframe.duration * index

    payload = json.dumps(
        {
            "event_type": "market.candle.closed",
            "payload": {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "open_time": open_time.isoformat(),
            },
        }
    )

    return StreamEntry(
        entry_id=entry_id or f"{symbol}-{timeframe.value}-{index}",
        fields={"payload": payload},
    )


class RecordingRunner:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, Timeframe, datetime]] = []
        self.fail_on = fail_on or set()

    async def run(self, symbol: str, timeframe: Timeframe, open_time: datetime) -> None:
        if symbol in self.fail_on:
            raise RuntimeError(f"detector blew up on {symbol}")

        self.calls.append((symbol, timeframe, open_time))


def test_a_payload_round_trips_into_the_close_it_announces() -> None:
    close = parse_close(entry("ETHUSDT", Timeframe.H4, index=2))

    assert close.symbol == "ETHUSDT"
    assert close.timeframe is Timeframe.H4
    assert close.open_time == BASE + Timeframe.H4.duration * 2


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"payload": ""},
        {"payload": "{not json"},
        {"payload": json.dumps({"payload": {"symbol": "BTCUSDT"}})},
        {"payload": json.dumps({"payload": {"symbol": "B", "timeframe": "H9", "open_time": "x"}})},
    ],
    ids=["no-field", "empty", "malformed", "missing-keys", "unknown-timeframe"],
)
def test_an_undescribable_entry_raises_rather_than_being_skipped(fields) -> None:
    """Skipping would drop a real close and ack it -- silently, forever.

    The relay copies the outbox payload verbatim, so anything unreadable here
    means the writer and this reader have drifted apart. That is a defect to
    surface, not a row to step over.
    """
    with pytest.raises(DomainInvariantError):
        parse_close(StreamEntry(entry_id="e1", fields=fields))


def test_higher_timeframes_are_detected_before_lower_ones() -> None:
    """HTF is context for LTF; an M5 processed first reads a stale H4."""
    ordered = order_for_detection(
        [
            parse_close(entry("BTCUSDT", Timeframe.M5)),
            parse_close(entry("BTCUSDT", Timeframe.H4)),
            parse_close(entry("BTCUSDT", Timeframe.H1)),
        ]
    )

    assert [close.timeframe for close in ordered] == [
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M5,
    ]


def test_one_context_stays_in_market_order() -> None:
    shuffled = [
        parse_close(entry(index=2)),
        parse_close(entry(index=0)),
        parse_close(entry(index=1)),
    ]

    ordered = order_for_detection(shuffled)

    assert [close.open_time for close in ordered] == [
        BASE,
        BASE + timedelta(hours=1),
        BASE + timedelta(hours=2),
    ]


@pytest.mark.asyncio
async def test_a_batch_is_detected_and_every_entry_acked() -> None:
    runner = RecordingRunner()

    report, acked = await CandleCloseConsumer(runner).consume([entry(index=0), entry(index=1)])

    assert report.received == 2
    assert report.processed == 2
    assert report.failed == 0
    assert len(acked) == 2
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_a_failing_context_is_left_unacked_and_the_others_still_run() -> None:
    """Two properties at once, and both matter.

    Unacked, because an entry that failed must come back on the next claim --
    acking it would discard the close. And the batch continues, because one
    broken symbol must not cost every other symbol its detection pass.
    """
    runner = RecordingRunner(fail_on={"ETHUSDT"})

    report, acked = await CandleCloseConsumer(runner).consume(
        [
            entry("BTCUSDT"),
            entry("ETHUSDT"),
            entry("SOLUSDT"),
        ]
    )

    assert report.received == 3
    assert report.processed == 2
    assert report.failed == 1

    assert "ETHUSDT-H1-0" not in acked
    assert set(acked) == {"BTCUSDT-H1-0", "SOLUSDT-H1-0"}

    assert {symbol for symbol, _, _ in runner.calls} == {"BTCUSDT", "SOLUSDT"}


@pytest.mark.asyncio
async def test_an_empty_batch_does_nothing() -> None:
    runner = RecordingRunner()

    report, acked = await CandleCloseConsumer(runner).consume([])

    assert report.received == 0
    assert acked == ()
    assert runner.calls == []


@pytest.mark.asyncio
async def test_one_unparseable_entry_does_not_stop_the_queue() -> None:
    """The poison pill that could stop the engine for good.

    `parse_close` raised from a list comprehension outside the try, so a single
    malformed entry propagated out of `consume`, killed the consumer task, and
    left the process alive with health green and nothing consuming. Every close
    after it would have been missed until someone noticed and restarted --
    which is precisely what G1b's "runs unattended >= 72 h" is meant to catch.
    """
    runner = RecordingRunner()

    report, acked = await CandleCloseConsumer(runner).consume(
        [
            StreamEntry(entry_id="1-1", fields={"payload": "not json at all"}),
            StreamEntry(
                entry_id="1-2",
                fields={
                    "payload": json.dumps(
                        {
                            "payload": {
                                "symbol": "BTCUSDT",
                                "timeframe": "H1",
                                "open_time": "2026-08-17T00:00:00+00:00",
                            }
                        }
                    )
                },
            ),
        ]
    )

    assert report.failed == 1
    assert [call[0] for call in runner.calls] == ["BTCUSDT"]
    assert acked == ("1-2",)
