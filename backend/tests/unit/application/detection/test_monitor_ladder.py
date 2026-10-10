"""The monitor driving a laddered M5 signal end to end (SLS v1.0.15 §12.3-§12.4).

Domain tests pin `walk_ladder`; these pin what only the monitor can get wrong:
WHICH candles it hands the walk. Two wrong answers are tempting and both are
silent:

* **From publication.** For a long, price travels DOWN to the entry, so the
  candles before the fill can sit above TP1. Counted as rungs, they would
  trail the stop to breakeven before the position existed.
* **Including the entry candle.** `observe` lets that candle only activate the
  signal, and the M5 measurement was re-run under that convention.

Each trap test builds a tape where the wrong answer produces a CLOSED_FLAT and
the right one leaves the signal live, so the two cannot agree by accident.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.detection.signal_monitor import SignalMonitorService
from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.application.ports.signals import SignalRecord
from scanner.domain.common import Candle, CandleSource
from scanner.domain.confluence import RISK_STOP
from scanner.domain.lifecycle import SignalState
from scanner.shared import Timeframe

TF = Timeframe.M5
T0 = datetime(2026, 10, 10, tzinfo=UTC)
STEP = timedelta(minutes=5)

# The live shape: zone 100-104, proximal 104, 1% risk stop 102.96, R 1.04,
# TP1 106.08, TP2 107.12.
ENTRY_AT = T0 + 2 * STEP


def bar(index: int, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TF,
        open_time=T0 + index * STEP,
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


def laddered_signal() -> SignalRecord:
    return SignalRecord(
        signal_id="sig-1",
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=TF,
        direction="UP",
        archetype="A3",
        grade="B",
        final_confidence=Decimal(72),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal("102.96"),
        target_bands=json.dumps(
            {
                "primary": {"low": "120", "high": "120", "pool_id": "p1"},
                "secondary": None,
                "ladder": {"start_r": "2", "step_r": "1", "unbounded": True},
            }
        ),
        published_at=T0,
        ttl_candles=48,
        algo_version="s8-test",
        param_set_version="test",
        payload=json.dumps({"invalidation": {"rule": RISK_STOP, "price": "102.96"}}),
        payload_hash="a" * 64,
        dedup_key="d",
    )


class Candles:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    async def fetch_series(self, symbol, timeframe, start, end):
        return [c for c in self.candles if start <= c.open_time < end]


class Signals:
    def __init__(self, row: SignalRecord) -> None:
        self.row = row

    async def get(self, signal_id):
        return self.row if signal_id == self.row.signal_id else None


class Transitions:
    """A signal that went ACTIVE on `ENTRY_AT` and is ACTIVE now."""

    def __init__(self) -> None:
        self.written: list[SignalTransitionRecord] = []

    async def list_live(self, symbol, timeframe):
        return ("sig-1",)

    async def current_state(self, signal_id):
        return SignalState.ACTIVE.value

    async def list_for_signal(self, signal_id):
        return (
            SignalTransitionRecord(
                transition_id="t-pub",
                signal_id=signal_id,
                from_state="DETECTED",
                to_state="PUBLISHED",
                at_candle_open_time=T0,
                recorded_at=T0,
                stress_test=False,
                refresh=False,
                trigger_evidence="{}",
            ),
            SignalTransitionRecord(
                transition_id="t-act",
                signal_id=signal_id,
                from_state="PUBLISHED",
                to_state="ACTIVE",
                at_candle_open_time=ENTRY_AT,
                recorded_at=ENTRY_AT,
                stress_test=False,
                refresh=False,
                trigger_evidence="{}",
            ),
        )

    async def append(self, transition):
        self.written.append(transition)
        return True


class Outcomes:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def append(self, outcome) -> bool:
        self.rows[outcome.signal_id] = outcome
        return True


class Clock:
    def now(self):
        return T0


def run_on(candles: list[Candle], at_index: int):
    transitions = Transitions()
    outcomes = Outcomes()
    svc = SignalMonitorService(
        Candles(candles),
        Signals(laddered_signal()),
        transitions,
        Clock(),
        outcomes,
    )

    return svc, transitions, outcomes, T0 + at_index * STEP


@pytest.mark.asyncio
async def test_tp1_then_back_to_the_entry_closes_flat_with_zero_r() -> None:
    svc, transitions, outcomes, at = run_on(
        [
            bar(2, "105", "103.9", "104.5"),  # the entry candle: only activates
            bar(3, "106.2", "104.5", "106"),  # TP1 (106.08): stop -> 104
            bar(4, "105", "103.95", "104"),  # back to the entry
        ],
        at_index=4,
    )

    await svc.run("BTCUSDT", TF, at)

    assert [t.to_state for t in transitions.written] == ["CLOSED_FLAT"]

    evidence = json.loads(transitions.written[0].trigger_evidence)
    assert evidence["rungs"] == "1"
    assert evidence["realised_r"] == "0"

    outcome = outcomes.rows["sig-1"]
    assert outcome.outcome == "CLOSED_FLAT"
    assert outcome.realised_r == Decimal(0), "the outcome must carry what was booked"


@pytest.mark.asyncio
async def test_tp2_then_back_to_tp1_succeeds_with_two_r_on_the_outcome() -> None:
    svc, transitions, outcomes, at = run_on(
        [
            bar(2, "105", "103.9", "104.5"),
            bar(3, "107.5", "104.5", "107"),  # TP2 (107.12): stop -> TP1 = 2R
            bar(4, "107", "106", "106.2"),  # back to TP1
        ],
        at_index=4,
    )

    await svc.run("BTCUSDT", TF, at)

    assert [t.to_state for t in transitions.written] == ["SUCCESS"]
    assert outcomes.rows["sig-1"].realised_r == Decimal(2)


@pytest.mark.asyncio
async def test_candles_before_the_fill_are_not_rungs() -> None:
    """Price came DOWN to the entry from above TP1. That travel is not a rung.

    Counted, bar 1 would trail the stop to 104 and bar 4's dip to 103.9 would
    close the signal flat. Not counted, the stop is still 102.96 and the
    signal is still live.
    """
    svc, transitions, _, at = run_on(
        [
            bar(1, "106.5", "105.5", "105.6"),  # before the fill, above TP1
            bar(2, "105", "103.9", "104.5"),  # the fill
            bar(3, "105.5", "104", "105"),  # no rung
            bar(4, "105", "103.9", "104"),  # above the real stop
        ],
        at_index=4,
    )

    await svc.run("BTCUSDT", TF, at)

    assert transitions.written == [], "pre-entry travel trailed the stop"


@pytest.mark.asyncio
async def test_the_entry_candle_is_not_a_rung_either() -> None:
    """The fill candle also reached TP1 -- but OHLC cannot say whether the high
    came before the fill or after, and `observe` only activates on it."""
    svc, transitions, _, at = run_on(
        [
            bar(2, "106.5", "103.9", "105"),  # fill AND a high above TP1
            bar(3, "105", "103.9", "104"),
        ],
        at_index=3,
    )

    await svc.run("BTCUSDT", TF, at)

    assert transitions.written == [], "the entry candle was counted as a rung"


@pytest.mark.asyncio
async def test_a_trailing_touch_records_no_stress_test() -> None:
    """Under the touch rule a wick through the stop IS the exit; there is no
    "wick through without failing" for `stress_test` to record."""
    svc, transitions, _, at = run_on(
        [
            bar(2, "105", "103.9", "104.5"),
            bar(3, "104.5", "102.9", "104"),  # wick through 102.96, close above
        ],
        at_index=3,
    )

    await svc.run("BTCUSDT", TF, at)

    assert [t.to_state for t in transitions.written] == ["FAILED"]
    assert transitions.written[0].stress_test is False
