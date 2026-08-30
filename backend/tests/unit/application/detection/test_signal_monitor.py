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


def signal(
    *,
    published_at: datetime = T0,
    ttl: int = 24,
    payload: str = "{}",
    direction: str = "UP",
) -> SignalRecord:
    return SignalRecord(
        signal_id="sig-1",
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=TF,
        direction=direction,
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
        payload=payload,
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


class FakeZoneContext:
    """`list_transitions` for exactly one zone, as the premise check asks."""

    def __init__(self, transitions_by_zone: dict[str, tuple] | None = None) -> None:
        self.transitions_by_zone = transitions_by_zone or {}

    async def list_transitions(self, zone_id: str):
        return self.transitions_by_zone.get(zone_id, ())


class FakeStructureEvidence:
    def __init__(self, records: tuple = ()) -> None:
        self.records = records

    async def list_structure(self, symbol, timeframe, start, end):
        return tuple(r for r in self.records if start <= r.event_at < end)


class FakeSetups:
    def __init__(self, rows: dict[str, object] | None = None) -> None:
        self.rows = rows or {}

    async def get(self, setup_id: str):
        return self.rows.get(setup_id)


class FakeEngineEvents:
    def __init__(self, records: tuple = ()) -> None:
        self.records = records

    async def list_events(self, symbol, timeframe, start, end):
        return tuple(r for r in self.records if start <= r.event_at < end)


def monitor(
    *,
    live=("sig-1",),
    state=SignalState.PUBLISHED.value,
    candles=None,
    zone_context=None,
    evidence=None,
    setups=None,
    events=None,
    **kw,
):
    transitions = FakeTransitions(live, state)
    outcomes = FakeOutcomes()

    svc = SignalMonitorService(
        FakeCandles(candles if candles is not None else []),
        FakeSignals([signal(**kw)]),
        transitions,
        FakeClock(),
        outcomes,
        zone_context=zone_context,
        evidence=evidence,
        setups=setups,
        events=events,
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


def _zone_payload(zone_id: str) -> str:
    return json.dumps({"entry_zone": {"zone_id": zone_id}})


def _zone_invalidated(zone_id: str, at: datetime):
    from scanner.application.ports.ict_zones import IctZoneTransitionRecord

    return IctZoneTransitionRecord(
        transition_id="zt-1",
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=TF,
        zone_type="FVG",
        from_state="FRESH",
        to_state="INVALIDATED",
        reason="close_through",
        transitioned_at=at,
        candle_index=497,
        evidence="{}",
    )


def _mss_invalidated(direction: str, at: datetime):
    from scanner.application.ports.ict_evidence import StructureEvidenceRecord

    return StructureEvidenceRecord(
        event_type=f"STRUCTURE_MSS_INVALIDATED_{direction}",
        event_at=at,
        algo_version="s6-structure-shift-v3",
        payload="{}",
    )


@pytest.mark.asyncio
async def test_an_invalidated_entry_zone_invalidates_the_signal_early() -> None:
    """§12.3: "zone violated ⇒ INVALIDATED_EARLY (pre-touch only)".

    The candle itself is quiet — nothing touches the entry — so the only
    thing that can move the signal is the premise check reading the zone's
    own transition rows.
    """
    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        zone_context=FakeZoneContext(
            {"zone-7": (_zone_invalidated("zone-7", T0 + timedelta(hours=2)),)}
        ),
        payload=_zone_payload("zone-7"),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 1
    assert transitions.written[0].to_state == "INVALIDATED_EARLY"

    evidence = json.loads(transitions.written[0].trigger_evidence)

    assert evidence["premise"] == "entry_zone_invalidated"


@pytest.mark.asyncio
async def test_an_mss_demotion_invalidates_the_signal_early() -> None:
    """§12.3: "MSS demoted" — the §3.6 reclaim event, direction-matched."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        evidence=FakeStructureEvidence((_mss_invalidated("UP", T0 + timedelta(hours=2)),)),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 1
    assert transitions.written[0].to_state == "INVALIDATED_EARLY"

    evidence = json.loads(transitions.written[0].trigger_evidence)

    assert evidence["premise"] == "mss_demoted_to_ranging"


@pytest.mark.asyncio
async def test_the_opposite_directions_demotion_is_not_this_premise() -> None:
    """An UP signal is premised on the bullish MSS; the bearish one dying
    says nothing about it."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        evidence=FakeStructureEvidence((_mss_invalidated("DOWN", T0 + timedelta(hours=2)),)),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_a_premise_broken_on_the_observed_candle_waits_one_candle() -> None:
    """Same-candle order is unknowable, and INVALIDATED_EARLY takes the
    signal out of the accounting — the exclusion is never awarded on the
    favourable reading (§15.4). The event is read on the next pass instead.
    """
    at = T0 + timedelta(hours=3)

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        evidence=FakeStructureEvidence(
            # event_at is THIS candle's close: strictly after `at`.
            (_mss_invalidated("UP", at + TF.duration),)
        ),
    )

    report = await svc.run("BTCUSDT", TF, at)

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_a_dead_premise_resolves_and_records_the_outcome() -> None:
    """INVALIDATED_EARLY is terminal, so §12.4's row is written with it."""

    svc, _ = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        zone_context=FakeZoneContext(
            {"zone-7": (_zone_invalidated("zone-7", T0 + timedelta(hours=1)),)}
        ),
        payload=_zone_payload("zone-7"),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.resolved == 1
    assert svc.outcomes.rows["sig-1"].outcome == "INVALIDATED_EARLY"


@pytest.mark.asyncio
async def test_a_zone_invalidated_on_a_later_candle_is_not_read_early() -> None:
    """§0.2 non-repaint: the monitor may replay a historical candle while
    the zone lifecycle has already recorded a LATER invalidation. That
    future fact must not reach back."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        zone_context=FakeZoneContext(
            {"zone-7": (_zone_invalidated("zone-7", T0 + timedelta(hours=5)),)}
        ),
        payload=_zone_payload("zone-7"),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_an_expired_entry_zone_is_not_a_violated_one() -> None:
    """§12.3 says "zone violated"; a zone that merely aged out asserts
    nothing about the premise being wrong."""

    from dataclasses import replace as dc_replace

    expired = dc_replace(
        _zone_invalidated("zone-7", T0 + timedelta(hours=2)),
        to_state="EXPIRED",
        reason="max_age",
    )

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        zone_context=FakeZoneContext({"zone-7": (expired,)}),
        payload=_zone_payload("zone-7"),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_a_down_signal_reads_its_own_directions_demotion() -> None:
    """The premise event type is built from the signal's direction, both
    ways round — an UP-only reading would leave every short unmonitored."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        evidence=FakeStructureEvidence((_mss_invalidated("DOWN", T0 + timedelta(hours=2)),)),
        direction="DOWN",
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 1
    assert transitions.written[0].to_state == "INVALIDATED_EARLY"


def _setup_with_sweep(pool_id: str | None):
    from scanner.application.ports.setups import SetupRecord

    contribution = {"code": "sweep_confirmed", "points": "20", "evidence_id": pool_id}

    return SetupRecord(
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=TF,
        direction="UP",
        archetype="A1",
        gate_results="{}",
        factor_scores="{}",
        adjustments="{}",
        base_confidence=Decimal(70),
        final_confidence=Decimal(75),
        floor_passed=True,
        algo_version="s8-test",
        evaluated_at=T0,
        evidence=json.dumps({"attribution": {"F2": [contribution]}}),
    )


def _reclaim_event(pool_id: str, at: datetime):
    from scanner.application.ports.detection import EngineEventRecord

    return EngineEventRecord(
        event_key=f"reclaim-{pool_id}-{at.isoformat()}",
        symbol="BTCUSDT",
        timeframe=TF,
        event_type="LIQUIDITY_SWEEP_RECLAIMED",
        event_at=at,
        algo_version="s5-test",
        payload=json.dumps({"pool_id": pool_id}),
        created_at=at,
    )


@pytest.mark.asyncio
async def test_the_seeding_sweeps_reclaim_invalidates_the_signal_early() -> None:
    """§12.3's third premise, wired through the setup's own attribution:
    the F2 sweep_confirmed contribution names the pool the setup stood on,
    and that pool's LIQUIDITY_SWEEP_RECLAIMED event kills the premise."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        setups=FakeSetups({"sig-1": _setup_with_sweep("pool-9")}),
        events=FakeEngineEvents((_reclaim_event("pool-9", T0 + timedelta(hours=2)),)),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 1
    assert transitions.written[0].to_state == "INVALIDATED_EARLY"

    evidence = json.loads(transitions.written[0].trigger_evidence)

    assert evidence["premise"] == "seeding_sweep_reclaimed"


@pytest.mark.asyncio
async def test_another_pools_reclaim_is_not_this_signals_premise() -> None:
    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        setups=FakeSetups({"sig-1": _setup_with_sweep("pool-9")}),
        events=FakeEngineEvents((_reclaim_event("pool-other", T0 + timedelta(hours=2)),)),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_a_setup_without_attribution_ids_stays_quiet() -> None:
    """Setups recorded before v26 carry evidence_id: null. The check must
    no-op for them rather than match a reclaim to nothing."""

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        setups=FakeSetups({"sig-1": _setup_with_sweep(None)}),
        events=FakeEngineEvents((_reclaim_event("pool-9", T0 + timedelta(hours=2)),)),
    )

    report = await svc.run("BTCUSDT", TF, T0 + timedelta(hours=3))

    assert report.transitions == 0
    assert transitions.written == []


@pytest.mark.asyncio
async def test_a_reclaim_on_the_observed_candle_waits_one_candle() -> None:
    """The same strictly-before rule as the other two premises."""

    at = T0 + timedelta(hours=3)

    svc, transitions = monitor(
        candles=[candle(3, high="108", low="106", close="107")],
        setups=FakeSetups({"sig-1": _setup_with_sweep("pool-9")}),
        events=FakeEngineEvents((_reclaim_event("pool-9", at + TF.duration),)),
    )

    report = await svc.run("BTCUSDT", TF, at)

    assert report.transitions == 0
    assert transitions.written == []
