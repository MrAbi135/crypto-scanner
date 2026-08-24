"""§12.3's monitor, over one closed candle."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.detection.signal_monitor import SignalMonitorService
from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.application.ports.signals import SignalRecord
from scanner.domain.common import Candle, CandleSource
from scanner.domain.lifecycle import SignalState
from scanner.shared import Timeframe

TF = Timeframe.H1
T0 = datetime(2026, 8, 24, tzinfo=UTC)


class FakeCandles:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    async def fetch_series(self, symbol, timeframe, start, end):
        return [c for c in self.candles if start <= c.open_time < end]


class FakeSignals:
    def __init__(self, rows: list[SignalRecord]) -> None:
        self.rows = {r.signal_id: r for r in rows}

    async def append(self, signal):
        self.rows[signal.signal_id] = signal
        return True

    async def latest_for_dedup_key(self, dedup_key):
        return None

    async def get(self, signal_id):
        return self.rows.get(signal_id)


class FakeTransitions:
    def __init__(self, live: tuple[str, ...], state: str) -> None:
        self.live = live
        self.state = state
        self.written: list[SignalTransitionRecord] = []
        self.seen: set[tuple[str, datetime]] = set()

    async def append(self, transition):
        key = (transition.signal_id, transition.at_candle_open_time)

        if key in self.seen:
            return False

        self.seen.add(key)
        self.written.append(transition)

        return True

    async def current_state(self, signal_id):
        return self.state

    async def list_live(self, symbol, timeframe):
        return self.live


class FakeClock:
    def now(self):
        return T0


def candle(index: int, *, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TF,
        open_time=T0 + timedelta(hours=index),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(100),
        quote_volume=Decimal(10000),
        taker_buy_volume=Decimal(50),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def signal(*, published_at: datetime = T0, ttl: int = 24) -> SignalRecord:
    return SignalRecord(
        signal_id="sig-1",
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=TF,
        direction="UP",
        archetype="A4",
        grade="A",
        final_confidence=Decimal(82),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal(98),
        target_bands=json.dumps(
            {"primary": {"low": "112", "high": "114", "pool_id": "p1"}, "secondary": None}
        ),
        published_at=published_at,
        ttl_candles=ttl,
        algo_version="s8-test",
        param_set_version="2026.08.24.2",
        payload="{}",
        payload_hash="a" * 64,
        dedup_key="BTCUSDT|H1|UP|A4|104.00:100.00",
    )


class FakeOutcomes:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def append(self, outcome) -> bool:
        if outcome.signal_id in self.rows:
            return False

        self.rows[outcome.signal_id] = outcome

        return True

    async def get(self, signal_id):
        return self.rows.get(signal_id)


def monitor(*, live=("sig-1",), state=SignalState.PUBLISHED.value, candles=None, **kw):
    transitions = FakeTransitions(live, state)
    outcomes = FakeOutcomes()

    svc = SignalMonitorService(
        FakeCandles(candles if candles is not None else []),
        FakeSignals([signal(**kw)]),
        transitions,
        FakeClock(),
        outcomes,
    )

    svc.outcomes = outcomes

    return svc, transitions


@pytest.mark.asyncio
async def test_a_touched_entry_activates_the_signal() -> None:
    svc, transitions = monitor(candles=[candle(3, high="120", low="103", close="118")])

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 1
    assert transitions.written[0].from_state == "PUBLISHED"
    assert transitions.written[0].to_state == "ACTIVE"


@pytest.mark.asyncio
async def test_a_wick_through_the_stop_is_recorded_without_moving_the_signal() -> None:
    """§12.3's `stress_test`, which is a fact about the candle.

    The row carries `from_state == to_state` because the signal did not move.
    A monitor that wrote nothing here would lose the one observation §12.3
    asks it to keep, and one that transitioned would fail a signal on a wick.
    """
    svc, transitions = monitor(
        state=SignalState.ACTIVE.value,
        candles=[candle(3, high="105", low="97", close="101")],
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert report.stress_tests == 1

    row = transitions.written[0]

    assert row.from_state == row.to_state == "ACTIVE"
    assert row.stress_test


@pytest.mark.asyncio
async def test_a_quiet_candle_writes_nothing() -> None:
    """One row per candle *that said something*.

    §12.3 monitors every close, but a close that changed nothing is not
    history -- writing it would grow T18 by one row per live signal per
    candle and bury the transitions that matter.
    """
    svc, transitions = monitor(
        state=SignalState.ACTIVE.value,
        candles=[candle(3, high="108", low="102", close="105")],
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert (report.transitions, report.stress_tests) == (0, 0)
    assert transitions.written == []


@pytest.mark.asyncio
async def test_the_same_candle_read_twice_records_once() -> None:
    """A replay is not a new fact.

    The transition id and the table's unique key are both the natural
    (signal, candle) pair, so the second write is refused by the repository
    and the monitor does not count it.
    """
    svc, transitions = monitor(candles=[candle(3, high="120", low="103", close="118")])

    at = T0 + timedelta(hours=3)

    first = await svc.run("BTCUSDT", TF, at)
    second = await svc.run("BTCUSDT", TF, at)

    assert first.transitions == 1
    assert second.transitions == 0
    assert len(transitions.written) == 1


@pytest.mark.asyncio
async def test_the_ttl_is_counted_from_the_publication_timestamp() -> None:
    """§12.5's TTL, without a counter on an append-only table.

    A stored counter would have to be updated on T17, which has no UPDATE
    surface -- and a monitor that missed a candle would then under-count for
    the rest of the signal's life. Timestamps cannot drift that way.
    """
    svc, transitions = monitor(
        state=SignalState.ACTIVE.value,
        ttl=24,
        candles=[candle(24, high="108", low="102", close="105")],
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=24))

    assert report.transitions == 1
    assert transitions.written[0].to_state == "EXPIRED_ACTIVE"


@pytest.mark.asyncio
async def test_a_candle_the_repository_has_not_stored_yet_changes_nothing() -> None:
    """The monitor is driven by a close event; the row can lag it.

    Reading an empty series as "no movement" would be wrong in the same way
    reading it as "expired" would be -- so it does neither and reports the
    live count it found.
    """
    svc, transitions = monitor(candles=[])

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.live_before == 1
    assert (report.transitions, report.stress_tests) == (0, 0)
    assert transitions.written == []


@pytest.mark.asyncio
async def test_the_levels_come_from_the_published_record() -> None:
    """§12.1: "evidence, zones, levels never mutate post-creation".

    The monitor reads T17's own columns rather than recomputing from live
    market state, which would silently re-aim a signal every time a zone moved
    underneath it. The target here is only in the stored payload -- a
    recomputation would have no way to know it.
    """
    svc, transitions = monitor(
        state=SignalState.ACTIVE.value,
        candles=[candle(3, high="113", low="105", close="110")],
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.resolved == 1
    assert transitions.written[0].to_state == "SUCCESS"


@pytest.mark.asyncio
async def test_resolving_writes_the_outcome_once() -> None:
    """§12.4's accounting lands when the signal resolves, and only then.

    The excursions come from the candles the signal lived through, which the
    monitor fetches at resolution rather than accumulating as it ran -- an
    accumulator would need updating on tables with no UPDATE surface.
    """
    lived = [
        candle(1, high="106", low="99", close="103"),
        candle(2, high="113", low="102", close="112"),
    ]

    svc, _ = monitor(state=SignalState.ACTIVE.value, candles=lived)

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=2))

    assert report.resolved == 1

    book = await svc.outcomes.get("sig-1")

    assert book is not None
    assert book.outcome == "SUCCESS"
    # Entry mid 102, R = 4. Best high 113 is 11 above = 2.75R; worst low 99 is
    # 3 below = 0.75R.
    assert book.mfe_r == Decimal("2.75")
    assert book.mae_r == Decimal("0.75")


@pytest.mark.asyncio
async def test_a_signal_that_does_not_resolve_gets_no_outcome_row() -> None:
    """T19 is "exactly one row per *resolved* signal".

    A stress test is not a resolution, and writing a row for one would put a
    live signal into the statistics.
    """
    svc, _ = monitor(
        state=SignalState.ACTIVE.value,
        candles=[candle(3, high="105", low="97", close="101")],
    )

    await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert await svc.outcomes.get("sig-1") is None
