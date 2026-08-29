"""Confluence replay: what it reads, what it refuses to guess, what it records."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore
from tests.support.builders import make_candle

from scanner.application.detection.confluence_replay import (
    CONFLUENCE_ALGO_VERSION,
    HTF_STATE_UNREACHABLE,
    UNREACHABLE_INPUTS,
    ConfluenceReplayService,
    _read_participation,
    _size_skew,
)
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_evidence import LiquidityEvidenceRecord
from scanner.application.ports.ict_zone_interactions import IctZoneInteractionRecord
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.application.ports.liquidity_detection import LiquidityPoolRecord
from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.domain.common import TradeAggregate
from scanner.domain.volume import WashRiskState
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)
TF = Timeframe.H4
END = BASE + TF.duration * 100


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, tzinfo=UTC)


# §6.2's RVOL baseline is 20 candles and §7.1's momentum warm-up is 30, so a
# context shorter than that is one G1 refuses rather than grades.
CANDLES = 60

# `make_series` closes its last candle here, and §8.2 G4 measures every zone
# against that price -- so a fixture zone has to sit on it, or the gate refuses
# the candidate before anything else is exercised.
LAST_CLOSE = Decimal(1000 + CANDLES - 1 + 1)
DOWN_LAST_CLOSE = Decimal(1000 - (CANDLES - 1) - 1)


def make_series(
    count: int = CANDLES,
    *,
    trend: str = "up",
    last_volume: str = "50",
    last_trades: int = 10,
    last_taker_buy: str | None = None,
) -> list:
    """A steadily trending context, with the newest candle's volume settable."""
    out = []

    if trend == "fading":
        return _fading_series(count, last_volume)

    for i in range(count):
        step = i if trend == "up" else -i
        base = Decimal(1000) + step

        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * i,
                open_=base,
                close=base + (1 if trend == "up" else -1),
                volume=Decimal(last_volume) if i == count - 1 else Decimal(50),
                # §6.4 asks whether the participant count rose with the
                # volume, so a fixture that spikes one without the other is
                # a wash signature by construction.
                trade_count=last_trades if i == count - 1 else 10,
                # §6.5(3) wants one-sided intent, which a balanced default
                # never shows.
                taker_buy_volume=(
                    Decimal(last_taker_buy)
                    if last_taker_buy is not None and i == count - 1
                    else None
                ),
            )
        )

    return out


# Every candle still closes up, so §7.1 keeps calling the direction UP -- the
# trend has not turned, it has run out of energy, which is the state §7.2 calls
# decelerating and §8.3.1 pays nothing for.
FADE_CANDLES = 10
FADE_BIG = Decimal(20)
FADE_SMALL = Decimal(1)

FADING_LAST_CLOSE = Decimal(1000) + FADE_BIG * (CANDLES - FADE_CANDLES) + FADE_SMALL * FADE_CANDLES


def _fading_series(count: int, last_volume: str) -> list:
    out = []
    price = Decimal(1000)

    for i in range(count):
        step = FADE_BIG if i < count - FADE_CANDLES else FADE_SMALL
        close = price + step

        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * i,
                open_=price,
                close=close,
                volume=Decimal(last_volume) if i == count - 1 else Decimal(50),
            )
        )

        price = close

    return out


class FakeCandleRepository:
    def __init__(self, series) -> None:
        self.series = list(series)

    async def fetch_series(self, symbol, timeframe, start, end):
        return self.series


class FakeEventRepository:
    """Holds the engines' prior output and accepts the confluence engine's."""

    def __init__(self, seeded: list[EngineEventRecord] | None = None) -> None:
        self.seeded = seeded or []
        self.appended: dict[str, EngineEventRecord] = {}

    async def append(self, record: EngineEventRecord) -> bool:
        if record.event_key in self.appended:
            return False

        self.appended[record.event_key] = record
        return True

    async def exists(self, event_key: str) -> bool:
        return event_key in self.appended

    async def list_events(self, symbol, timeframe, start, end):
        return tuple(self.seeded)


class FakeZoneRepository:
    def __init__(self, zones: list[IctZoneRecord]) -> None:
        self.zones = zones
        self.asked_versions: object = "never asked"

    async def list_live(self, symbol, timeframe, *, only_versions=None):
        # Recorded so the contract test below can assert scoring pins its
        # read. The fixture zones themselves are served unfiltered: these
        # tests build zones with fixture evidence, and filtering here would
        # make every existing test's zones vanish for the wrong reason.
        self.asked_versions = only_versions

        return tuple(self.zones)


class FakeSymbols:
    """§6.6's symbol-level tag, which §6.7 caps F4 on."""

    def __init__(self, wash_risk: bool = False) -> None:
        self.state = WashRiskState(tagged=wash_risk)

    async def get_wash_risk(self, exchange_symbol: str) -> WashRiskState:
        return self.state


class FakeTradeAggregates:
    """§6.5's T4 minute buckets. Empty unless a test supplies them."""

    def __init__(self, items: list | None = None) -> None:
        self.items = items or []

    async def append_many(self, aggregates) -> int:
        return 0

    async def list_between(self, symbol: str, start, end) -> tuple:
        return tuple(item for item in self.items if start <= item.minute < end)


class FakePools:
    """§4.5's ACTIVE pool map, which §8.3.1 reads the target term from."""

    def __init__(self, items: list | None = None) -> None:
        self.items = items or []

    async def list_active(self, symbol: str, timeframe) -> tuple:
        return tuple(self.items)


class FakeInteractions:
    """§5.9 history, keyed by zone."""

    def __init__(
        self,
        items: dict[str, list] | None = None,
        respected: bool = False,
    ) -> None:
        self.items = items or {}
        self.respected = respected

    async def append(self, interaction) -> bool:
        return True

    async def any_respect_at(self, symbol, timeframe, observed_at) -> bool:
        return self.respected

    async def list_for_zone(self, zone_id: str) -> tuple:
        return tuple(
            sorted(self.items.get(zone_id, []), key=lambda i: (i.candle_index, i.interaction_id))
        )


def interaction(zone_id: str, kind: str, index: int) -> IctZoneInteractionRecord:
    return IctZoneInteractionRecord(
        interaction_id=f"{zone_id}:{kind}:{index}",
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=TF,
        zone_type="BREAKER",
        kind=kind,
        observed_at=BASE + TF.duration * index,
        candle_index=index,
        penetration_depth=Decimal("0.2"),
        close_price=LAST_CLOSE,
        rejection_wick=Decimal("0.4"),
        close_through=False,
        evidence="{}",
    )


class FakeEvidenceRepository:
    def __init__(self, liquidity: list[LiquidityEvidenceRecord] | None = None) -> None:
        self.liquidity = liquidity or []

    async def list_structure(self, symbol, timeframe, start, end):
        raise AssertionError(
            "confluence must not read the SWING_*/STRUCTURE_* slice -- it needs "
            "BOS, CHOCH, MSS, sweeps and participation too"
        )

    async def list_liquidity(self, symbol, timeframe, start, end):
        return tuple(self.liquidity)


def event(event_type: str, index: int = 1, **payload) -> EngineEventRecord:
    at = BASE + TF.duration * index

    return EngineEventRecord(
        event_key=f"{event_type}-{index}",
        symbol="BTCUSDT",
        timeframe=TF,
        event_type=event_type,
        event_at=at,
        algo_version="test",
        payload=json.dumps(payload),
        created_at=at,
    )


def failed_break_event(
    index: int,
    direction: str,
    recorded_index: int | None = None,
) -> EngineEventRecord:
    """§3.5's failed break, as `structure_replay` writes it.

    `index` fixes when the failure happened. `recorded_index` is the offset the
    payload was stamped with, which in production comes from whichever window
    recorded it and never changes afterwards -- so the two can disagree.
    """
    at = BASE + TF.duration * index

    return EngineEventRecord(
        event_key=f"STRUCTURE_FAILED_BREAK_{direction}-{index}",
        symbol="BTCUSDT",
        timeframe=TF,
        event_type=f"STRUCTURE_FAILED_BREAK_{direction}",
        event_at=at,
        algo_version="test",
        payload=json.dumps(
            {
                "failed": True,
                "failed_index": (index if recorded_index is None else recorded_index),
                "direction": direction,
            }
        ),
        created_at=at,
    )


def swing_event(index: int, label: str, kind: str = "HIGH") -> EngineEventRecord:
    """A §3.3-labelled swing, as `structure_replay` writes it.

    STRUCTURE_EXTERNAL_*, because that is the event carrying the label --
    `_persist_swing`'s SWING_* payload has none. Writing this helper against
    SWING_* is what let the first version of `_read_labels` pass its tests
    while reading nothing at all in production.

    `event()` cannot be used: its own second parameter is named `index`, and
    §7.4 needs the candle index inside the payload as well.
    """
    at = BASE + TF.duration * index

    return EngineEventRecord(
        event_key=f"STRUCTURE_EXTERNAL_{label}-{index}",
        symbol="BTCUSDT",
        timeframe=TF,
        event_type=f"STRUCTURE_EXTERNAL_{label}",
        event_at=at,
        algo_version="test",
        payload=json.dumps({"index": index, "label": label}),
        created_at=at,
    )


def zone(
    zone_id: str = "z1",
    *,
    polarity: str = "BULLISH",
    zone_type: str = "OB",
    grade: str = "OB_A",
    state: str = "FRESH",
    confirmed_index: int = 5,
    created_at: datetime | None = None,
    band_low: Decimal | None = None,
    band_high: Decimal | None = None,
    evidence: str = "{}",
) -> IctZoneRecord:
    # A zone confirmed at index N was created by the candle at index N, and
    # §5.4 stamps an FVG with that candle's close time. Defaulting the two to
    # agree keeps a fixture from claiming to be recent by its index while its
    # timestamp says otherwise -- which is the state the real table was in,
    # and what `test_a_zone_older_than_its_index_claims...` is about.
    if created_at is None:
        created_at = BASE + TF.duration * (confirmed_index + 1)

    return IctZoneRecord(
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=TF,
        zone_type=zone_type,
        polarity=polarity,
        state=state,
        grade=grade,
        band_low=LAST_CLOSE - 1 if band_low is None else band_low,
        band_high=LAST_CLOSE + 1 if band_high is None else band_high,
        refined_low=None,
        refined_high=None,
        created_index=confirmed_index,
        confirmed_index=confirmed_index,
        created_at=created_at,
        updated_at=created_at,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=None,
        evidence=evidence,
    )


LAST_INDEX = CANDLES - 1

# §4.6 gives a sweep 15 closed candles of setup relevance, so a sweep confirmed
# 5 candles back is comfortably live and one from the start of the window is not.
RECENT_SWEEP = LAST_INDEX - 5


def sweep(
    *,
    side: str = "SSL",
    liquidity_class: str = "EXTERNAL",
    confirmed_index: int = RECENT_SWEEP,
    recorded_index: int | None = None,
    reclaimed: bool = False,
    depth_atr: str = "0.9",
) -> LiquidityEvidenceRecord:
    return LiquidityEvidenceRecord(
        pool_id="p1",
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="liquidity_sweep",
        transitioned_at=BASE + TF.duration * confirmed_index,
        # `confirmed_index` fixes when the sweep happened. `recorded_index` is
        # the offset the row was stamped with, which in production comes from
        # whichever window recorded it and then never changes -- so the two
        # can disagree, and on the VM they overwhelmingly do.
        candle_index=(confirmed_index if recorded_index is None else recorded_index),
        evidence=json.dumps(
            {
                "side": side,
                "liquidity_class": liquidity_class,
                "sweep_depth_atr": depth_atr,
                "reclaimed": reclaimed,
                # Still written, because the liquidity engine writes it --
                # but nothing reads it now, and no test may set it
                # independently of `confirmed_index`. Expressing a sweep's age
                # through this number instead of its timestamp is the defect,
                # not a way to describe one.
                "setup_expiry_index": (
                    (confirmed_index if recorded_index is None else recorded_index) + 15
                ),
            }
        ),
    )


SHIFT_ALGO = "s4b-shift-test"

# The rung above H4 is D1 (contexts._LADDER), so that is where an HTF state has
# to be written for this fixture's candidates to read one.
HTF = Timeframe.D1


def service(
    *,
    events: list[EngineEventRecord] | None = None,
    zones: list[IctZoneRecord] | None = None,
    liquidity: list[LiquidityEvidenceRecord] | None = None,
    candles: list | None = None,
    htf_trend: str | None = None,
    interactions: dict[str, list] | None = None,
    respected: bool = False,
    pools: list | None = None,
    minutes: list | None = None,
    wash_risk: bool = False,
    setups=None,
    signals=None,
    incidents=None,
    transitions=None,
    metrics=None,
):
    repo = FakeEventRepository(events)

    store = InMemoryEngineStateStore()
    shift_state = EngineStateManager(store, namespace=SHIFT_NAMESPACE)

    if htf_trend is not None:
        # Written straight into the store rather than through `save`, so the
        # fixture stays synchronous and every test keeps the same two-value
        # unpack it had before the ladder existed.
        store.values[shift_state.context_key("BTCUSDT", HTF.value, SHIFT_ALGO)] = json.dumps(
            asdict(
                StructureEngineState(
                    symbol="BTCUSDT",
                    timeframe=HTF.value,
                    algo_version=SHIFT_ALGO,
                    trend_state=htf_trend,
                )
            )
        )

    svc = ConfluenceReplayService(
        FakeCandleRepository(make_series() if candles is None else candles),
        repo,
        FakeZoneRepository(zones or []),
        FakeEvidenceRepository(liquidity),
        FakeInteractions(interactions, respected),
        FakePools(pools),
        FakeTradeAggregates(minutes),
        FakeSymbols(wash_risk),
        FakeClock(),
        shift_state,
        shift_algo_version=SHIFT_ALGO,
        setups=setups,
        signals=signals,
        incidents=incidents,
        transitions=transitions,
        metrics=metrics,
    )

    return svc, repo


class FakeTransitions:
    """T18 in memory, unique on (signal, candle, refresh) as migration 017 is."""

    def __init__(self) -> None:
        self.rows: list = []
        self._seen: set = set()

    async def append(self, transition) -> bool:
        key = (
            transition.signal_id,
            transition.at_candle_open_time,
            transition.refresh,
        )

        if key in self._seen:
            return False

        self._seen.add(key)
        self.rows.append(transition)

        return True

    async def current_state(self, signal_id):
        states = [r.to_state for r in self.rows if r.signal_id == signal_id and not r.refresh]

        return states[-1] if states else None

    async def list_live(self, symbol, timeframe):
        return ()

    @property
    def refreshes(self) -> list:
        return [r for r in self.rows if r.refresh]


class FakeSignals:
    """T17 in memory: insert-once, and no update method to be tempted by."""

    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def append(self, signal) -> bool:
        if signal.signal_id in self.rows:
            return False

        self.rows[signal.signal_id] = signal

        return True

    async def latest_for_dedup_key(self, dedup_key):
        matching = [r for r in self.rows.values() if r.dedup_key == dedup_key]

        if not matching:
            return None

        return max(matching, key=lambda r: r.published_at)

    async def get(self, signal_id):
        return self.rows.get(signal_id)


class FakeIncidents:
    """§2.15's open-incident view, which §15.3(2) reads for feed cleanliness."""

    def __init__(self, open_for: tuple[str, ...] = ()) -> None:
        self.open_for = open_for

    async def list_open(self, symbol=None):
        return [object()] if symbol in self.open_for else []


class FakeSetups:
    """T16, in memory. Append-only, like the table it stands in for."""

    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def append(self, setup) -> bool:
        if setup.setup_id in self.rows:
            return False

        self.rows[setup.setup_id] = setup

        return True

    async def list_at(self, symbols, timeframe, evaluated_at):
        return tuple(
            r
            for r in self.rows.values()
            if r.symbol in symbols and r.timeframe is timeframe and r.evaluated_at == evaluated_at
        )


async def run(svc, trend_state: str = "RANGING"):
    return await svc.run("BTCUSDT", TF, BASE, END, trend_state=trend_state)


def pool(
    *,
    price: Decimal,
    side: str = "BSL",
    liquidity_class: str = "EXTERNAL",
    strength: Decimal = Decimal(80),
) -> LiquidityPoolRecord:
    return LiquidityPoolRecord(
        pool_id=f"p-{side}-{price}",
        symbol="BTCUSDT",
        timeframe=TF,
        side=side,
        liquidity_class=liquidity_class,
        source="SWING",
        price=price,
        band_low=price,
        band_high=price,
        strength=strength,
        state="ACTIVE",
        member_count=1,
        created_index=0,
        created_at=BASE,
        updated_at=BASE,
        evidence="{}",
    )


def bullish_setup() -> dict:
    """A context that clears every reachable gate in the UP direction."""
    return {
        "events": [
            event("BOS_UP", 3, direction="UP"),
            event("MSS_UP", 6, direction="UP"),
        ],
        "zones": [zone("z1")],
        "liquidity": [sweep()],
    }


@pytest.mark.asyncio
async def test_an_empty_window_grades_nothing() -> None:
    svc, repo = service(candles=[])

    report = await run(svc)

    assert report.candidates == ()
    assert report.events_inserted == 0
    assert repo.appended == {}


@pytest.mark.asyncio
async def test_a_direction_with_no_zone_is_blocked_and_says_which_gate() -> None:
    """A blocked candidate must name its gate.

    "No setup" and "gate G4 found no zone" are indistinguishable in a count, and
    only one of them is a reason to go looking for a bug.
    """
    svc, repo = service(events=[event("BOS_UP", 3, direction="UP")])

    report = await run(svc)

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert up.failed_gates
    assert up.confidence is None
    assert repo.appended == {}


@pytest.mark.asyncio
async def test_a_qualifying_context_is_graded_and_recorded() -> None:
    svc, repo = service(**bullish_setup())

    report = await run(svc)

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.confidence is not None and up.confidence > 0
    assert up.zone_id == "z1"
    assert report.events_inserted == 1
    assert next(iter(repo.appended.values())).event_type == "SETUP_CANDIDATE_UP"


@pytest.mark.asyncio
async def test_the_record_carries_the_gate_results_and_the_attribution_tree() -> None:
    """S8's DoD, which the record did not meet.

    It asks for "gate battery (G1-G7 with *recorded results*)" and for every
    candidate to carry "the full factor evidence tree". The engine computed
    both -- `SetupCandidate` has `gates_passed` and `failed_gates`, and every
    `*_factor` returns its `contributions` -- and `_record` wrote neither,
    taking `.score` at the call site and dropping the rest.

    The cost was not theoretical. Asked which gate blocks setups on the soak
    VM, the database could not answer: 82 recorded candidates, no gate field
    on any of them. The question had to be guessed at by re-reading the code.

    Absent is not the same as zero, so the two locally scored factors -- ZONE
    and VOLUME, whose scores are computed here rather than from enumerated
    contributions -- carry no tree rather than an empty one that would read as
    "nothing earned it".
    """
    svc, repo = service(**bullish_setup())

    await run(svc, "BULLISH")

    recorded = [
        json.loads(r.payload)
        for r in repo.appended.values()
        if r.event_type.startswith("SETUP_CANDIDATE_")
    ]

    assert recorded

    for payload in recorded:
        assert "gates_passed" in payload
        assert isinstance(payload["failed_gates"], list)

        # F1 is scored from enumerated contributions, so its tree must be
        # there and must add up to something a reader can check.
        tree = payload["attribution"]["F1"]

        assert tree
        assert all({"code", "points", "evidence_id"} <= set(item) for item in tree)

    # ZONE and VOLUME have no enumerated contributions yet, and say so by
    # being absent rather than by an empty list.
    assert "F3" not in recorded[0]["attribution"]
    assert "F4" not in recorded[0]["attribution"]


@pytest.mark.asyncio
async def test_a_gate_passing_candidate_is_stored_in_t16() -> None:
    """DDD T16: "every confluence candidate that passed gates".

    The engine wrote its candidates only to the event log until now, which is
    an audit trail rather than the modelled record the product reads. The row
    carries what calibration needs and the event never did: the base
    confidence beside the final one, the adjustments that moved it, and the
    factor attribution.
    """
    setups = FakeSetups()

    svc, _ = service(**bullish_setup(), setups=setups)

    await run(svc, "BULLISH")

    stored = list(setups.rows.values())

    assert stored

    row = stored[0]

    assert row.symbol == "BTCUSDT"
    assert row.direction in {"UP", "DOWN"}
    assert row.base_confidence > 0
    assert row.final_confidence > 0

    adjustments = json.loads(row.adjustments)

    # §8.5 caps both sides, and whether a cap bound is the difference between
    # "no more evidence" and "more than the doctrine will pay for".
    assert {"applied", "synergy", "penalty", "synergy_capped", "penalty_capped"} <= set(adjustments)

    assert json.loads(row.evidence)["attribution"]["F1"]


@pytest.mark.asyncio
async def test_a_gate_failing_candidate_is_not_stored_in_t16() -> None:
    """T16 holds gate-passers, and a gate failure has nothing to calibrate.

    There is no scored evidence behind it -- `base_confidence` would have to
    be invented to store one -- so it stays in the event log, where the fact
    that it was evaluated and refused is still recorded.
    """
    setups = FakeSetups()

    # No zone, so G4 cannot pass in either direction.
    svc, _ = service(events=[event("BOS_UP", 3, direction="UP")], setups=setups)

    await run(svc, "BULLISH")

    assert setups.rows == {}


@pytest.mark.asyncio
async def test_the_setup_row_is_append_only_across_a_re_evaluated_candle() -> None:
    """T16's read/write pattern is append-only.

    Replaying the same close must not rewrite what the first pass concluded,
    and the id is a hash of symbol, timeframe, direction, close and algo
    version -- real values only. The §5.9 interaction ids were hashed over a
    sliding-window offset and wrote the same fact twenty times over; this is
    the same shape of key built the way that one should have been.
    """
    setups = FakeSetups()

    svc, _ = service(**bullish_setup(), setups=setups)

    await run(svc, "BULLISH")

    first = dict(setups.rows)

    await run(svc, "BULLISH")

    assert setups.rows.keys() == first.keys()


def publishable_candidate(direction: str = "UP"):
    """A candidate that has already cleared §8.6, built by hand.

    The scoring fixtures in this file top out at 41 and the only series in the
    repository that clears §8.2 scores the same, so there is no way to reach
    §15.3 through `run` today. Testing `_publish` directly is the honest
    alternative: it proves the write path, and it does not pretend the
    scoring produced something it did not.
    """
    from scanner.application.detection.confluence_replay import SetupCandidate
    from scanner.domain.confluence import (
        ZONE_DISTAL_EDGE,
        SignalLevels,
        TargetBand,
        entry_zone,
    )
    from scanner.domain.confluence.levels import Invalidation
    from scanner.domain.lifecycle import SignalPayload

    levels = SignalLevels(
        direction=direction,
        entry=entry_zone(
            zone_id="z1", direction=direction, band_low=Decimal(100), band_high=Decimal(104)
        ),
        invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(
            low=Decimal(112), high=Decimal(114), pool_id="p1", strength=Decimal(60)
        ),
    )

    payload = SignalPayload(
        symbol="BTCUSDT",
        timeframe=TF.value,
        direction=direction,
        evidence_ids=("z1",),
        confidence=Decimal(82),
        grade="A",
        factors={"F1": "70"},
        archetype="A4",
        reason="A4 long: first touch of a displacement gap in the trend direction.",
        invalidation_distance_atr=Decimal("1.2"),
        invalidation_distance_pct=Decimal("0.9"),
        r_multiple=Decimal("2.5"),
        condition_tags=(),
        levels=levels,
        htf_chain={"H4": "SIGNAL", "HTF": "BULLISH"},
        algo_version="s8-test",
        param_set_version="2026.08.24.2",
    )

    return SetupCandidate(
        symbol="BTCUSDT",
        timeframe=TF,
        direction=direction,
        gates_passed=True,
        failed_gates=(),
        confidence=Decimal(82),
        grade="A",
        archetype="A4",
        publishable=True,
        levels=levels,
        payload=payload,
        zone_id="z1",
    )


@pytest.mark.asyncio
async def test_a_clean_candidate_is_published_and_sealed() -> None:
    """The path the other three cannot reach.

    Without this, `_publish` could be an empty function and every suppression
    test in this file would still pass -- they all assert that nothing was
    written.

    What the row must carry: §15.2's levels extracted for querying, the sealed
    payload, the hash over exactly that payload, and §10.3's dedup key.
    """
    signals = FakeSignals()
    candidate = publishable_candidate()

    svc, _ = service(**bullish_setup(), signals=signals, incidents=FakeIncidents())

    await svc._publish("BTCUSDT", TF, BASE + TF.duration * 10, candidate)

    assert len(signals.rows) == 1

    row = next(iter(signals.rows.values()))

    assert row.grade == "A"
    assert row.archetype == "A4"
    assert (row.entry_proximal, row.entry_distal) == (Decimal(104), Decimal(100))
    assert row.invalidation_level == Decimal(98)
    assert row.dedup_key == "BTCUSDT|H4|UP|A4|104.00:100.00"

    # The hash is over the stored payload and nothing else, so a row can
    # always be re-verified from its own two columns.
    import hashlib

    assert hashlib.sha256(row.payload.encode("utf-8")).hexdigest() == row.payload_hash

    # §12.5's TTL for this timeframe travels with the signal -- §12.3 needs it
    # to know when to stop watching.
    assert row.ttl_candles == 18


@pytest.mark.asyncio
async def test_a_second_signal_on_a_live_key_merges_as_a_refresh() -> None:
    """§10.3: a signal matching a live one "is merged as a refresh event on
    the existing signal (evidence appended) -- never a second alert".

    A merge, not a suppression. The distinction is the whole clause: a
    suppression files the re-detection away from the signal it belongs to,
    and the thing a reader needs later is whether *this* signal's setup was
    still standing on that candle.
    """
    signals = FakeSignals()
    transitions = FakeTransitions()
    candidate = publishable_candidate()

    svc, repo = service(
        **bullish_setup(),
        signals=signals,
        incidents=FakeIncidents(),
        transitions=transitions,
    )

    at = BASE + TF.duration * 10

    await svc._publish("BTCUSDT", TF, at, candidate)
    await svc._publish("BTCUSDT", TF, at + TF.duration, candidate)

    assert len(signals.rows) == 1

    published_id = next(iter(signals.rows))

    assert len(transitions.refreshes) == 1

    row = transitions.refreshes[0]

    # Appended to the signal that holds the key -- not to a new one, and not
    # to nothing.
    assert row.signal_id == published_id
    assert row.at_candle_open_time == at + TF.duration
    # A refresh does not move the signal, so it sits where the signal is.
    assert row.from_state == row.to_state == "PUBLISHED"
    assert not row.stress_test

    # "Never a second alert", and never a suppression either: a merge is not
    # a candidate that failed a check.
    assert not [r for r in repo.appended.values() if r.event_type.startswith("SIGNAL_SUPPRESSED_")]


@pytest.mark.asyncio
async def test_the_refresh_does_not_change_what_the_signal_was_published_with() -> None:
    """§12.1: "evidence, zones, levels never mutate post-creation (refresh
    events append)".

    The second detection here scores higher than the first. Its number lands
    in the refresh row and nowhere else -- a published signal whose confidence
    crept upward every candle would make the grade on the alert a fiction.
    """
    signals = FakeSignals()
    transitions = FakeTransitions()

    svc, _ = service(
        **bullish_setup(),
        signals=signals,
        incidents=FakeIncidents(),
        transitions=transitions,
    )

    at = BASE + TF.duration * 10

    first = publishable_candidate()

    await svc._publish("BTCUSDT", TF, at, first)

    published = next(iter(signals.rows.values()))
    original = published.final_confidence

    await svc._publish("BTCUSDT", TF, at + TF.duration, publishable_candidate())

    assert next(iter(signals.rows.values())).final_confidence == original

    evidence = json.loads(transitions.refreshes[0].trigger_evidence)

    assert evidence["dedup_key"] == published.dedup_key


@pytest.mark.asyncio
async def test_a_duplicate_that_also_fails_another_check_is_suppressed_not_merged() -> None:
    """A merge is only for a candidate that would otherwise have published.

    §10.3 merges the re-detection because it is the *same setup, still
    valid*. One that also had a stale feed in its evidence chain is not that
    -- it would have been refused on a free key too, and appending it to a
    live signal would hand that signal evidence §15.3(2) just rejected.
    """
    signals = FakeSignals()
    transitions = FakeTransitions()

    svc, repo = service(
        **bullish_setup(),
        signals=signals,
        incidents=FakeIncidents(),
        transitions=transitions,
    )

    at = BASE + TF.duration * 10

    await svc._publish("BTCUSDT", TF, at, publishable_candidate())

    stale = replace(publishable_candidate(), stale_context=True)

    await svc._publish("BTCUSDT", TF, at + TF.duration, stale)

    assert transitions.refreshes == []

    suppressed = [
        json.loads(r.payload)
        for r in repo.appended.values()
        if r.event_type.startswith("SIGNAL_SUPPRESSED_")
    ]

    assert suppressed == [{"reasons": ["STALE_FEEDS", "DUPLICATE_KEY"]}]


@pytest.mark.asyncio
async def test_a_resolved_signal_frees_its_key_before_the_ttl_lapses() -> None:
    """§10.3 holds a key for a *live* signal, and §12's terminal states end that.

    Without the state check the key would stay held for the rest of the TTL
    after the signal hit its target -- muting a genuinely new setup on the
    same zone for up to eighteen candles, and doing it silently.
    """
    signals = FakeSignals()
    transitions = FakeTransitions()

    svc, _ = service(
        **bullish_setup(),
        signals=signals,
        incidents=FakeIncidents(),
        transitions=transitions,
    )

    at = BASE + TF.duration * 10

    await svc._publish("BTCUSDT", TF, at, publishable_candidate())

    published = next(iter(signals.rows.values()))

    await transitions.append(
        SignalTransitionRecord(
            transition_id="resolved",
            signal_id=published.signal_id,
            from_state="PUBLISHED",
            to_state="SUCCESS",
            at_candle_open_time=at + TF.duration,
            recorded_at=at + TF.duration,
            stress_test=False,
            refresh=False,
            trigger_evidence="{}",
        )
    )

    # Still well inside H4's 18-candle TTL.
    await svc._publish("BTCUSDT", TF, at + TF.duration * 2, publishable_candidate())

    assert len(signals.rows) == 2
    assert transitions.refreshes == []


@pytest.mark.asyncio
async def test_the_key_frees_once_the_first_signal_outlives_its_ttl() -> None:
    """§10.3 merges against an *ACTIVE* signal, not against history.

    A zone that produced a signal in January is free to produce another in
    March, which is why the dedup query returns the latest row and the TTL
    arithmetic happens in the engine rather than in SQL.
    """
    signals = FakeSignals()
    candidate = publishable_candidate()

    svc, _ = service(**bullish_setup(), signals=signals, incidents=FakeIncidents())

    at = BASE + TF.duration * 10

    await svc._publish("BTCUSDT", TF, at, candidate)
    # H4's TTL is 18 candles; one past it frees the key.
    await svc._publish("BTCUSDT", TF, at + TF.duration * 19, candidate)

    assert len(signals.rows) == 2


@pytest.mark.asyncio
async def test_a_candidate_below_its_floor_publishes_no_signal() -> None:
    """§8.6's floor decides, and this fixture scores 41.

    T17 holds published signals; a candidate that never cleared its floor is
    recorded in the event log and in T16 and stops there. A signals table that
    filled up regardless would make the funnel §14 monitors meaningless.
    """
    signals = FakeSignals()

    svc, _ = service(**bullish_setup(), signals=signals, incidents=FakeIncidents())

    await run(svc, "BULLISH")

    assert signals.rows == {}


@pytest.mark.asyncio
async def test_an_open_incident_suppresses_rather_than_publishes() -> None:
    """§15.3(2): "all feeds fresh at publish moment".

    G1's `data_ready` is not this check -- its own comment says it covers the
    warm-up and still owes freshness -- so reading it here would have made
    §15.3(2) unfailable. An open incident on the symbol is the feed not being
    clean, and §2.15 is where that is recorded.
    """
    signals = FakeSignals()

    svc, repo = service(
        **bullish_setup(),
        signals=signals,
        incidents=FakeIncidents(open_for=("BTCUSDT",)),
    )

    await run(svc, "BULLISH")

    assert signals.rows == {}

    # Nothing suppressed here either -- the candidate never reached §15.3,
    # because it never cleared its floor. The check above is what proves the
    # incident view is wired; this asserts the two paths do not interfere.
    assert not [r for r in repo.appended.values() if r.event_type.startswith("SIGNAL_SUPPRESSED_")]


@pytest.mark.asyncio
async def test_g2_reads_the_engines_trend_state_not_the_last_break() -> None:
    """The bug this test exists for.

    Inferring the trend from the last BOS in the window graded a 58-confidence
    UP candidate on real BTCUSDT H1 while §3.7's state machine had the context
    at BEARISH. BOS does not move trend state; CHoCH does. The state is the
    structure engine's to own, so confluence is handed it.
    """
    setup = bullish_setup()

    # No live sweep either, so the reversal branch has nothing to stand on.
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]
    setup["liquidity"] = []

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G2" in up.failed_gates


@pytest.mark.asyncio
async def test_a_matching_trend_state_opens_the_trend_following_branch() -> None:
    setup = bullish_setup()
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed


@pytest.mark.asyncio
async def test_a_live_sweep_clears_g2_against_the_prevailing_trend() -> None:
    """§8.2 G2's second branch: "reversal: ... Sweep-Reversal conditions §8.6"."""
    svc, _ = service(**bullish_setup())

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed


@pytest.mark.asyncio
async def test_a_stale_mss_does_not_clear_g2_by_itself() -> None:
    """The bug this test exists for.

    §3.6 says an MSS flips the trend, so a live one already reaches G2 through
    the state. Treating "any MSS in the window" as a second branch let a
    two-month-old reversal pass the gate: on real BTCUSDT H1 the window held
    four MSS_UP and four MSS_DOWN, so both directions cleared G2 on every run.
    """
    setup = bullish_setup()
    setup["liquidity"] = []

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G2" in up.failed_gates


@pytest.mark.asyncio
async def test_a_sweep_past_its_setup_expiry_no_longer_seeds_a_reversal() -> None:
    """§4.6: relevance expires 15 closed candles after confirmation.

    Nothing read the expiry at all once, so a sweep from the far end of an
    1849-candle replay counted as present evidence of current flow. Then it
    was read as `last_index <= setup_expiry_index`, and both of those are
    offsets in windows that had long since parted company -- 78,265 of the
    87,605 sweeps on the VM carried an expiry below the window's right edge
    and so were dead on arrival, and the rest could never expire.

    It is counted in time now, and this sweep is 59 candles behind the one
    being scored.
    """
    setup = bullish_setup()
    setup["liquidity"] = [sweep(confirmed_index=1)]
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed


@pytest.mark.asyncio
async def test_a_sweep_whose_index_says_live_but_whose_clock_says_expired() -> None:
    """The state nearly every sweep on the VM is actually in.

    Liveness was `last_index <= setup_expiry_index`. `last_index` is the right
    edge of the current 500-candle window, always. `setup_expiry_index` is
    `confirmed_index + 15` computed in whichever window first saw the sweep,
    and nothing rewrites it. A sweep confirmed at the right edge is stamped
    with an expiry fifteen candles *past* the edge, and since the edge is
    where the comparison always stands, it stays live for the rest of time.

    Measured on the soak VM over 87,605 sweep transitions: 78,265 carry an
    expiry below 500 -- dead from the moment they were written, whatever
    their age -- and the remaining 9,340 sit at or above it and can never
    expire. Not one of them was ever fifteen candles from anything.

    This sweep happened 40 candles ago, well past §4.6's fifteen, and carries
    an index from a window that has long since moved on. It is expired, and
    only the clock can say so.
    """
    setup = bullish_setup()
    setup["liquidity"] = [sweep(confirmed_index=LAST_INDEX - 40, recorded_index=LAST_INDEX)]
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed


@pytest.mark.asyncio
async def test_a_sweep_of_the_wrong_side_does_not_support_the_direction() -> None:
    """Sweeping resting sell-side liquidity clears the way up, not down."""
    setup = bullish_setup()
    setup["liquidity"] = [sweep(side="BSL")]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BEARISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed


@pytest.mark.asyncio
async def test_a_reclaimed_sweep_is_contrary_evidence_not_support() -> None:
    """§4.6: "consumers must treat reclaimed sweeps as contrary evidence".

    An SSL sweep that was later reclaimed is price closing back below the low it
    took -- the bullish read failed. So it is contrary evidence for UP, and it
    must neither support UP nor leave G5 quietly passing.
    """
    setup = bullish_setup()
    setup["liquidity"] = [sweep(reclaimed=True)]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G5" in up.failed_gates


@pytest.mark.asyncio
async def test_a_caution_state_is_not_treated_as_an_endorsement() -> None:
    """§3.7 enters CAUTION when a CHoCH prints against the trend.

    That is the moment the trend is in question, so it is not a trend-following
    pass. The reversal branch is still open to it once an MSS confirms.
    """
    setup = bullish_setup()
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]
    setup["liquidity"] = []

    svc, _ = service(**setup)

    report = await run(svc, trend_state="BULLISH_CAUTION")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed


@pytest.mark.asyncio
async def test_a_ranging_state_with_a_live_sweep_stays_gradeable() -> None:
    """A5 Range Liquidity Play is defined *on* RANGING (§8.6).

    A G2 that only accepts a matching trend state deletes the archetype outright.
    """
    setup = bullish_setup()
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]

    svc, _ = service(**setup)

    report = await run(svc, trend_state="RANGING")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed


@pytest.mark.asyncio
async def test_ranging_without_a_sweep_has_nothing_to_stand_on() -> None:
    setup = bullish_setup()
    setup["events"] = [e for e in setup["events"] if e.event_type != "MSS_UP"]
    setup["liquidity"] = []

    svc, _ = service(**setup)

    report = await run(svc, trend_state="RANGING")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed


@pytest.mark.asyncio
async def test_confluence_reads_every_engine_not_the_ict_structure_slice() -> None:
    """The bug this test exists for.

    `IctEvidenceRepository.list_structure` returns only SWING_* and STRUCTURE_*.
    Reading trend from it makes every BOS/CHOCH/MSS check False, so G2 fails
    forever and the engine reports zero setups on every market -- which is
    indistinguishable from a quiet market. `FakeEvidenceRepository` raises if
    that slice is touched.
    """
    svc, _ = service(**bullish_setup())

    report = await run(svc)

    assert any(c.gates_passed for c in report.candidates)


@pytest.mark.asyncio
async def test_an_fvg_in_open_state_earns_its_state_points() -> None:
    """§5 spells the same two facts two ways: OPEN/TOUCHED and FRESH/TESTED.

    §8.3.1's table names only the second. Without the bridge every FVG, IFVG and
    BPR scores zero state points -- not for being stale, but for spelling
    "untouched" differently.
    """
    setup = bullish_setup()

    fresh = service(**{**setup, "zones": [zone("z1", grade="FVG", state="FRESH")]})
    open_ = service(**{**setup, "zones": [zone("z1", grade="FVG", state="OPEN")]})

    a = next(c for c in (await run(fresh[0])).candidates if c.direction == "UP")
    b = next(c for c in (await run(open_[0])).candidates if c.direction == "UP")

    assert a.factors["F3"] == b.factors["F3"]
    assert a.factors["F3"] > 0


@pytest.mark.asyncio
async def test_the_best_zone_in_the_stack_is_scored_not_the_newest() -> None:
    setup = bullish_setup()

    svc, _ = service(
        **{
            **setup,
            "zones": [
                zone("strong", grade="BRK_A", state="FRESH", confirmed_index=2),
                zone("recent", grade="IFVG", state="TESTED", confirmed_index=9),
            ],
        }
    )

    report = await run(svc)

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.zone_id == "strong"


@pytest.mark.asyncio
async def test_unreachable_inputs_are_named_in_the_stored_record() -> None:
    """A stored confidence whose missing inputs are not stated cannot be read
    honestly a month later.

    Nothing is structurally unreadable any more, so what a record names now is
    conditional: this fixture writes no state for the rung above, and the
    series sits outside a dealing range.
    """
    svc, repo = service(**bullish_setup())

    await run(svc)

    payload = json.loads(next(iter(repo.appended.values())).payload)

    assert HTF_STATE_UNREACHABLE in payload["unreachable_inputs"]

    # §4.5 selects the target pool now, so naming it here would understate a
    # score that did read it.
    assert "target_pool_strength" not in payload["unreachable_inputs"]

    # Nothing wrote a state for the timeframe above, so F6 was defaulted --
    # and the record says which of its inputs was read and which was not.
    assert HTF_STATE_UNREACHABLE in payload["unreachable_inputs"]


@pytest.mark.asyncio
async def test_a_live_unreclaimed_sweep_is_paid_as_unclaimed_and_fresh() -> None:
    """§8.3.1's "+6 unclaimed" and "+6 fresh" grade the sweep, not a pool.

    They were passed False on the reading that they described the target pool,
    which cost every swept setup 12 points the evidence already supported.
    """
    setup = bullish_setup()

    with_sweep, _ = service(**setup)
    without, _ = service(**{**setup, "liquidity": []})

    a = next(c for c in (await run(with_sweep, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    assert b.factors["F2"] == Decimal(0)

    # Every term §8.3.1 lists for a sweep, spelled out so re-hardcoding either
    # of the last two shows up here as 46.8 rather than as a passing test:
    # 20 confirmed + 16 external + 12 x 0.9 depth + 6 unclaimed + 6 fresh.
    assert a.factors["F2"] == Decimal("58.8")


@pytest.mark.asyncio
async def test_sweep_quality_grades_one_sweep_not_the_best_half_of_several() -> None:
    """§8.3.1 reads as a single sweep's quality.

    Taking `any(external)` and `max(depth)` across the set paid 60 -- the F2
    sweep ceiling -- to a pair where the external sweep was shallow and the
    deep one was internal. No sweep there was both.
    """
    setup = bullish_setup()

    svc, _ = service(
        **{
            **setup,
            "liquidity": [
                sweep(liquidity_class="EXTERNAL", depth_atr="0.1"),
                sweep(liquidity_class="INTERNAL", depth_atr="1.0"),
            ],
        }
    )

    a = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    # The external one, which is what §8.3.1 pays most for:
    # 20 + 16 external + 12 x 0.1 depth + 6 + 6. Mixing the two gave 60.
    assert a.factors["F2"] == Decimal("49.2")


@pytest.mark.asyncio
async def test_an_expired_sweep_pays_nothing_at_all() -> None:
    """`supporting` drops it before scoring, so no term survives it."""
    setup = bullish_setup()

    expired, _ = service(**{**setup, "liquidity": [sweep(confirmed_index=0)]})
    without, _ = service(**{**setup, "liquidity": []})

    a = next(c for c in (await run(expired, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F2"] == b.factors["F2"]


@pytest.mark.asyncio
async def test_the_nearest_external_pool_ahead_of_price_pays_the_target_term() -> None:
    """§4.5: "nearest opposing external pool = default target zone"."""
    setup = bullish_setup()

    with_pool, _ = service(**setup, pools=[pool(price=LAST_CLOSE + 10)])
    without, _ = service(**setup)

    a = next(c for c in (await run(with_pool, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    # 80 / 100 x 25.
    assert a.factors["F2"] - b.factors["F2"] == Decimal(20)


@pytest.mark.asyncio
async def test_a_pool_price_has_already_passed_is_not_a_target() -> None:
    """§4.2: "BSL pools only relevant while price is below them".

    Side alone would count a pool the move has left behind, and on real
    BTCUSDT H1 most BSL pools are behind price rather than ahead of it.
    """
    setup = bullish_setup()

    behind, _ = service(**setup, pools=[pool(price=LAST_CLOSE - 10)])
    without, _ = service(**setup)

    a = next(c for c in (await run(behind, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F2"] == b.factors["F2"]


@pytest.mark.asyncio
async def test_an_internal_pool_is_not_the_target_and_nearest_wins() -> None:
    """§4.4 reserves internal liquidity for entry refinement, and of the
    external ones §4.5 takes the nearest -- not the strongest anywhere above."""
    setup = bullish_setup()

    svc, _ = service(
        **setup,
        pools=[
            pool(price=LAST_CLOSE + 2, liquidity_class="INTERNAL", strength=Decimal(100)),
            pool(price=LAST_CLOSE + 5, strength=Decimal(40)),
            pool(price=LAST_CLOSE + 50, strength=Decimal(100)),
        ],
    )
    without, _ = service(**setup)

    a = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    # The nearest external pool is the 40-strength one: 40 / 100 x 25 = 10.
    assert a.factors["F2"] - b.factors["F2"] == Decimal(10)


@pytest.mark.asyncio
async def test_f4_is_the_published_volume_factor_not_the_rvol_ratio() -> None:
    """§8.3.1: "Volume Factor Score as published" (§6.7), whose base is 50.

    `volume_factor` passes a 0-100 score through unmodified and the call site
    handed it `reading.rvol` — between 0 and about 6 on real data. F4 was
    contributing roughly one point in a hundred where the design allots
    fifteen percent of the confidence.
    """
    svc, _ = service(**bullish_setup())

    candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    assert candidate.factors["F4"] == Decimal(50)


@pytest.mark.asyncio
async def test_a_spike_with_real_participation_pays_its_fifteen() -> None:
    setup = bullish_setup()

    svc, _ = service(**setup, candles=make_series(last_volume="250", last_trades=40))

    candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    assert candidate.factors["F4"] == Decimal(65)


@pytest.mark.asyncio
async def test_a_spike_on_the_same_trade_count_is_capped_at_neutral() -> None:
    """§6.4 tags it and §6.7 caps it: "hard cap 50 if ... `suspect_volume`".

    Five times the volume on an unchanged trade count is one account cycling
    size. The assertion was meaningless while F4 carried the RVOL ratio — the
    score could not reach 50, so the cap could not bind.
    """
    setup = bullish_setup()

    svc, _ = service(**setup, candles=make_series(last_volume="250"))

    candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    # 50 base + 15 aligned spike = 65, capped back to 50.
    assert candidate.factors["F4"] == Decimal(50)


@pytest.mark.asyncio
async def test_a_spike_the_other_way_costs_twenty() -> None:
    """Below the cap, so the integrity tag does not lift it either."""
    setup = bullish_setup()

    svc, _ = service(
        **{
            **setup,
            "zones": [zone("z1", band_low=DOWN_LAST_CLOSE - 1, band_high=DOWN_LAST_CLOSE + 1)],
        },
        # 350, not 250: the down series prices lower, and 250 x ~941 falls
        # under §6.2's absolute $250k quote floor, so no spike confirms.
        candles=make_series(trend="down", last_volume="350"),
    )

    candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    assert candidate.factors["F4"] == Decimal(30)


@pytest.mark.asyncio
async def test_momentum_pointing_the_other_way_is_not_counted_as_support() -> None:
    setup = bullish_setup()

    aligned, _ = service(**setup)

    # The down series closes elsewhere, so its zone has to move with it or G4
    # refuses the candidate and there is no F5 to compare.
    opposed, _ = service(
        **{
            **setup,
            "zones": [zone("z1", band_low=DOWN_LAST_CLOSE - 1, band_high=DOWN_LAST_CLOSE + 1)],
        },
        candles=make_series(trend="down"),
    )

    a = next(c for c in (await run(aligned, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(opposed, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F5"] > b.factors["F5"]


@pytest.mark.asyncio
async def test_a_context_too_short_to_measure_is_refused_by_g1() -> None:
    """§6.2 needs 20 candles of baseline and §7.1 thirty of warm-up.

    A context that cannot produce either reading is not one §8 can grade, and
    `data_ready` used to be a bare True.
    """
    svc, _ = service(**bullish_setup(), candles=make_series(15))

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G1" in up.failed_gates


@pytest.mark.asyncio
async def test_a_replay_is_idempotent() -> None:
    """The engine re-runs a trailing window on every close.

    A service that inserted afresh each time would multiply every candidate by
    the window length.
    """
    svc, repo = service(**bullish_setup())

    first = await run(svc)
    second = await run(svc)

    assert first.events_inserted == 1
    assert second.events_inserted == 0
    assert len(repo.appended) == 1


@pytest.mark.asyncio
async def test_the_algo_version_is_stamped_on_the_record() -> None:
    svc, repo = service(**bullish_setup())

    await run(svc)

    assert next(iter(repo.appended.values())).algo_version == CONFLUENCE_ALGO_VERSION


@pytest.mark.asyncio
async def test_an_inverted_window_is_refused() -> None:
    svc, _ = service()

    with pytest.raises(ValueError, match="end must be greater"):
        await svc.run("BTCUSDT", TF, BASE, BASE)


@pytest.mark.asyncio
async def test_f6_reads_the_state_of_the_timeframe_above() -> None:
    """The bug this test exists for.

    `htf_state` was hardcoded to RANGING, so `htf_alignment_factor` took the
    same branch on every candidate in both directions -- F6 was a constant, and
    §8.3 weights it at 15% of the confidence.
    """
    setup = bullish_setup()

    up_htf, _ = service(**setup, htf_trend="BULLISH")
    down_htf, _ = service(**setup, htf_trend="BEARISH")

    a = next(c for c in (await run(up_htf, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(down_htf, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F6"] > b.factors["F6"]


@pytest.mark.asyncio
async def test_caution_toward_the_direction_scores_between_aligned_and_opposed() -> None:
    """§3.7's CAUTION carries a direction, and F6's table keeps it."""
    setup = bullish_setup()

    scores = {}

    for label, trend in (
        ("aligned", "BULLISH"),
        ("caution", "BULLISH_CAUTION"),
        ("opposed", "BEARISH"),
    ):
        svc, _ = service(**setup, htf_trend=trend)

        candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

        scores[label] = candidate.factors["F6"]

    assert scores["aligned"] > scores["caution"] > scores["opposed"]


@pytest.mark.asyncio
async def test_an_unread_ladder_is_named_rather_than_scored_as_ranging() -> None:
    """The timeframe-ladder failure, one level up.

    A neighbour that was never replayed yields a plausible number rather than
    an error. F6 still takes its neutral value -- there is nothing better to
    take -- but the record distinguishes "the HTF is ranging" from "nobody
    looked", which are the same 15% otherwise.
    """
    setup = bullish_setup()

    unread, _ = service(**setup)
    ranging, _ = service(**setup, htf_trend="RANGING")

    a = next(c for c in (await run(unread, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(ranging, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F6"] == b.factors["F6"]
    assert HTF_STATE_UNREACHABLE in a.unreachable
    assert HTF_STATE_UNREACHABLE not in b.unreachable


@pytest.mark.asyncio
async def test_the_report_and_its_candidates_name_the_same_gaps() -> None:
    """They disagreed, and the CLI printed the wrong one.

    The report's `unreachable` defaulted to the module constant while the
    candidates computed theirs, so on a context whose ladder could not be read
    `engine run` announced `htf: unread` and, one line down, an unreachable
    list that did not mention `htf_state`.
    """
    svc, _ = service(**bullish_setup())

    report = await run(svc, "BULLISH")

    assert report.htf_state is None
    assert HTF_STATE_UNREACHABLE in report.unreachable

    for candidate in report.candidates:
        assert candidate.unreachable == report.unreachable


@pytest.mark.asyncio
async def test_a_read_ladder_leaves_the_report_clean() -> None:
    svc, _ = service(**bullish_setup(), htf_trend="BEARISH")

    report = await run(svc, "BULLISH")

    assert report.htf_state == "DOWN"
    assert HTF_STATE_UNREACHABLE not in report.unreachable


@pytest.mark.asyncio
async def test_a_zone_far_from_price_does_not_satisfy_g4() -> None:
    """The bug this test exists for.

    §8.2 G4 asks for a zone "whose band contains or is adjacent (<= 0.5 x ATR)
    to current price". Only the polarity half was implemented, so any live zone
    passed wherever price stood -- on real BTCUSDT H1, 9 BULLISH and 16 BEARISH
    zones, and the gate could not fail. DOWN reached grade B there with its
    nearest zone 302.7 away against a 60.97 tolerance.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("far", band_low=Decimal(100), band_high=Decimal(101))]

    svc, _ = service(**setup)

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G4" in up.failed_gates


@pytest.mark.asyncio
async def test_a_zone_containing_price_satisfies_g4() -> None:
    svc, _ = service(**bullish_setup())

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").gates_passed


@pytest.mark.asyncio
async def test_a_zone_just_below_price_is_adjacent_enough() -> None:
    """The band need not contain price; §8.2 allows half an ATR of gap."""
    setup = bullish_setup()
    setup["zones"] = [zone("near", band_low=LAST_CLOSE - 3, band_high=LAST_CLOSE - Decimal("0.5"))]

    svc, _ = service(**setup)

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").gates_passed


@pytest.mark.asyncio
async def test_f3_scores_a_qualifying_zone_not_the_best_one_anywhere() -> None:
    """Scoring a zone price is nowhere near is the same error one layer down.

    A distant BRK_A outranks a nearby FVG on grade points alone, so without the
    same filter F3 would report the quality of a zone the setup is not at.
    """
    setup = bullish_setup()
    setup["zones"] = [
        zone("distant_strong", grade="BRK_A", band_low=Decimal(100), band_high=Decimal(101)),
        zone("near_weak", grade="IFVG"),
    ]

    svc, _ = service(**setup)

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.zone_id == "near_weak"


def series_with_displacement(*, at: int = 45, count: int = CANDLES) -> list:
    """The same ramp, with one §5.10 displacement candle in it.

    A smooth series contains no displacement at all, so an A4 chain built on it
    would fail for the wrong reason and the test would prove nothing.
    """
    out = []

    for i in range(count):
        base = Decimal(1000) + i

        if i == at:
            out.append(
                make_candle(
                    timeframe=TF,
                    open_time=BASE + TF.duration * i,
                    open_=base,
                    close=base + 20,
                    high=base + 20,
                    low=base - 1,
                    volume=Decimal(50),
                )
            )
        else:
            out.append(
                make_candle(
                    timeframe=TF,
                    open_time=BASE + TF.duration * i,
                    open_=base,
                    close=base + 1,
                    volume=Decimal(50),
                )
            )

    return out


def impulse_then_pullback() -> list:
    """A displaced impulse up, then a confirmed retracement -- §8.6 A3's shape.

    Two external swings are the minimum for one leg and three for two, and
    `k_ext = 5` needs five clear candles either side of each, which is why the
    turns sit where they do. Verified against `segment_legs`: IMPULSE UP
    11->43 with `displaced=True`, then RETRACEMENT DOWN 43->53 as the current
    anchoring leg. The displacement candle is at 25, far enough back that
    §8.2 G5's `displaced_recently` stays false and the pullback is a
    retracement rather than counter-displacement.
    """
    levels = [1040 - 4 * i for i in range(11)]
    levels += [1000 + 2 * i for i in range(1, 15)]
    levels += [1054]
    levels += [1054 + 2 * i for i in range(1, 18)]
    levels += [levels[-1] - 4 * i for i in range(1, 11)]
    levels += [levels[-1] + i for i in range(1, 8)]

    out = []

    for i, level in enumerate(levels):
        previous = levels[i - 1] if i else level

        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * i,
                open_=Decimal(previous),
                close=Decimal(level),
                high=Decimal(max(previous, level)) + 1,
                low=Decimal(min(previous, level)) - 1,
                volume=Decimal(50),
            )
        )

    return out


@pytest.mark.asyncio
async def test_a_displaced_break_inside_the_impulse_classifies_as_a3() -> None:
    """§8.6 A3, which had never classified and had no test.

    Its `displaced_bos` term asked whether any BOS fell inside the impulse leg
    being retraced, and compared the leg's bounds -- offsets in the current
    window -- against `break_index` from the event payload, an offset in
    whichever window recorded the break. Two windows, one comparison.

    No fixture in this suite ever put `break_index` in a BOS payload, so the
    set was empty in every test and A3 could not classify; in production the
    numbers were real but unrelated, so it classified by coincidence. Neither
    is a rule.

    Both sides are times now. The break here sits at index 30, inside the
    11->43 impulse, and the current leg is the retracement off its high.
    """
    last_close = int(impulse_then_pullback()[-1].close)

    setup = bullish_setup()
    setup["events"] = [event("BOS_UP", 30, direction="UP")]
    setup["zones"] = [
        zone(
            "ob",
            band_low=Decimal(last_close - 1),
            band_high=Decimal(last_close + 1),
        )
    ]

    svc, _ = service(**setup, candles=impulse_then_pullback(), htf_trend="BULLISH")

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.archetype == "A3"


@pytest.mark.asyncio
async def test_a_fresh_displacement_fvg_at_price_classifies_as_a4() -> None:
    """§8.6 A4: displacement FVG, first touch in trend direction, HTF aligned.

    Until G4 established that price is *at* a zone, no §8.6 chain could close
    and `classify_archetype` returned None on every candidate ever scored.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=46)]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.archetype == "A4"


@pytest.mark.asyncio
async def test_a_zone_older_than_its_index_claims_is_too_old_for_a4() -> None:
    """A4's age gate read two numbers from two different windows.

    `fvg_age_candles` was `max(0, last_index - confirmed_index)`. `last_index`
    is the right edge of the current 500-candle window. `confirmed_index` is
    the offset the zone had when it was first detected -- and the zone upsert
    does not list that column in its `set_` clause, so it is written once and
    never moves. A zone is almost always detected at the right edge, so the
    subtraction returned nearly zero no matter how long the zone had been
    sitting there.

    Measured on the soak VM across 6,388 non-terminal FVG zones: the gate
    `fvg_age_candles <= 30` admitted 982, where the age implied by their
    timestamps admits 227. The oldest zone was 690 candles old and read as
    200 -- and 690 is past the window, so no index could have described it.

    The zone here is the A4 fixture with one thing changed: its index still
    says 13 candles back, its timestamp says it formed before the window
    opened. It is too old for A4, and only the timestamp can say so.
    """
    setup = bullish_setup()
    setup["zones"] = [
        zone(
            "fvg",
            zone_type="FVG",
            grade="FVG",
            state="OPEN",
            confirmed_index=46,
            created_at=BASE - TF.duration * 40,
        )
    ]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    # Everything else about it still qualifies -- G4 passed, the displacement
    # is there, it is a first touch. Age alone disqualifies it.
    assert up.gates_passed
    assert up.archetype is None


@pytest.mark.asyncio
async def test_an_fvg_with_no_displacement_behind_it_does_not_classify() -> None:
    """§5.4 detects gaps without asking what made them.

    "Displacement FVG" is a narrower claim than "FVG", and the zone record does
    not carry it -- so it is recovered from §5.10, and an ordinary gap must not
    pass as one.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=46)]

    svc, _ = service(**setup, htf_trend="BULLISH")

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype is None


@pytest.mark.asyncio
async def test_an_already_touched_fvg_is_not_a_first_touch() -> None:
    setup = bullish_setup()
    setup["zones"] = [
        zone("fvg", zone_type="FVG", grade="FVG", state="CE_FILLED", confirmed_index=46)
    ]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype is None


@pytest.mark.asyncio
async def test_a4_needs_the_htf_behind_it() -> None:
    """§8.6 A4 requires HTF alignment; a counter-HTF continuation is not one."""
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=46)]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BEARISH",
    )

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype is None


@pytest.mark.asyncio
async def test_a_stale_fvg_is_past_its_age_limit() -> None:
    """§8.6 A4: "FVG age <= 30 candles"."""
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=5)]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(at=7),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype is None


@pytest.mark.asyncio
async def test_the_unreachable_list_does_not_disown_a_chain_that_closed() -> None:
    """A4 classifies, so the record must not still claim archetypes are out of
    reach -- the entry names the three chains that genuinely are."""
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=46)]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.archetype == "A4"
    # Every §8.6 chain is reachable now, so no archetype entry should survive.
    assert not [name for name in up.unreachable if name.startswith("archetype")]


@pytest.mark.asyncio
async def test_classification_and_publication_are_separate_decisions() -> None:
    """§8.6: below-floor candidates "are recorded internally ... never published".

    Matching a chain is not the same as clearing its floor, and conflating the
    two would publish every context that merely looked like a setup.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("fvg", zone_type="FVG", grade="FVG", state="OPEN", confirmed_index=46)]

    svc, _ = service(
        **setup,
        candles=series_with_displacement(),
        htf_trend="BULLISH",
    )

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.archetype == "A4"
    assert up.confidence is not None
    assert up.publishable is (up.confidence >= Decimal(70))


@pytest.mark.asyncio
async def test_a_breaker_respected_on_its_first_retest_classifies_as_a2() -> None:
    """§8.6 A2: "Breaker formed -> first retest with Respect (§5.9)".

    §5.9's interactions were write-only until this change, so A2 could never
    close no matter what the market did.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("brk", zone_type="BREAKER", grade="BRK_A", state="TESTED")]

    svc, _ = service(
        **setup,
        htf_trend="BULLISH",
        interactions={"brk": [interaction("brk", "RESPECT", 40)]},
    )

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.archetype == "A2"


@pytest.mark.asyncio
async def test_a_breaker_that_failed_its_first_retest_is_not_a2() -> None:
    """The first retest, not any of them.

    A zone that was violated and only later respected is a different story from
    one that held on the first approach, and A2 is about the second.
    """
    setup = bullish_setup()
    setup["zones"] = [zone("brk", zone_type="BREAKER", grade="BRK_A", state="TESTED")]

    svc, _ = service(
        **setup,
        htf_trend="BULLISH",
        interactions={
            "brk": [
                interaction("brk", "VIOLATION", 30),
                interaction("brk", "RESPECT", 40),
            ]
        },
    )

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype != "A2"


@pytest.mark.asyncio
async def test_a_breaker_with_no_interaction_history_is_not_a2() -> None:
    setup = bullish_setup()
    setup["zones"] = [zone("brk", zone_type="BREAKER", grade="BRK_A", state="TESTED")]

    svc, _ = service(**setup, htf_trend="BULLISH")

    assert (
        next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP").archetype
        != "A2"
    )


@pytest.mark.asyncio
async def test_an_entry_confirmation_pays_its_f3_points() -> None:
    """§8.3.1 gives the §5.9 Confirmation 10 points, and it was hardcoded off."""
    setup = bullish_setup()

    without, _ = service(**setup, htf_trend="BULLISH")
    with_confirmation, _ = service(
        **setup,
        htf_trend="BULLISH",
        interactions={"z1": [interaction("z1", "CONFIRMATION", 50)]},
    )

    a = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(with_confirmation, "BULLISH")).candidates if c.direction == "UP")

    assert b.factors["F3"] - a.factors["F3"] == Decimal(10)


# §5.7's range is the most recent confirmed external swing on each side that
# bracket price. A monotonic ramp has no external swings at all, so every PD
# assertion on `make_series` would pass on an absent context.
RANGE_LOW = Decimal(954)
RANGE_HIGH = Decimal(1100)


def ranged_series(settle: int) -> list:
    """A peak, then a trough, then price settling between them."""
    out = [
        make_candle(
            timeframe=TF,
            open_time=BASE + TF.duration * i,
            open_=Decimal(1000) + Decimal(i) / 100,
            close=Decimal(1000) + Decimal(i) / 100 + Decimal("0.05"),
            high=Decimal(1000) + Decimal(i) / 100 + Decimal("0.1"),
            low=Decimal(1000) + Decimal(i) / 100 - Decimal("0.1"),
            volume=Decimal(50),
        )
        for i in range(300)
    ]

    for k in range(12):
        base = Decimal(1003 + k * 8)
        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * (300 + k),
                open_=base,
                close=base + 8,
                high=base + 9,
                low=base - 1,
                volume=Decimal(50),
            )
        )

    for k in range(24):
        base = Decimal(1099 - k * 6)
        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * (312 + k),
                open_=base,
                close=base - 6,
                high=base + 1,
                low=base - 7,
                volume=Decimal(50),
            )
        )

    for k in range(14):
        out.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * (336 + k),
                open_=Decimal(settle),
                close=Decimal(settle),
                high=Decimal(settle + 1),
                low=Decimal(settle - 1),
                volume=Decimal(50),
            )
        )

    return out


# `ranged_series` is 350 candles, so the default sweep -- sized for the
# 60-candle fixture -- is long past §4.6's 15-candle relevance and silently
# absent, taking A1's `external_sweep` with it.
RANGED_LAST = 349


def ranged_setup(settle: int) -> dict:
    setup = bullish_setup()
    setup["zones"] = [zone("z1", band_low=Decimal(settle - 1), band_high=Decimal(settle + 1))]
    setup["liquidity"] = [sweep(confirmed_index=RANGED_LAST - 5)]

    return setup


@pytest.mark.asyncio
async def test_g3_blocks_a_long_in_premium() -> None:
    """§5.7's directional gate: "long-side setups require range_position <= 0.5".

    G3 was `pd_context_ok=True`, so it could not fail -- the doctrine's *where*
    filter was absent from the engine entirely.
    """
    settle = 1050  # (1050 - 954) / 146 = 0.6575, premium

    svc, _ = service(**ranged_setup(settle), candles=ranged_series(settle))

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert not up.gates_passed
    assert "G3" in up.failed_gates


@pytest.mark.asyncio
async def test_g3_allows_a_long_in_discount() -> None:
    settle = 990  # (990 - 954) / 146 = 0.2466, discount

    svc, _ = service(**ranged_setup(settle), candles=ranged_series(settle))

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").gates_passed


@pytest.mark.asyncio
async def test_the_short_side_gate_mirrors_it() -> None:
    settle = 1050

    svc, _ = service(**ranged_setup(settle), candles=ranged_series(settle))

    report = await run(svc, "BEARISH")

    down = next(c for c in report.candidates if c.direction == "DOWN")

    assert "G3" not in down.failed_gates


@pytest.mark.asyncio
async def test_a_readable_pd_context_is_not_reported_as_a_gap() -> None:
    settle = 990

    svc, _ = service(**ranged_setup(settle), candles=ranged_series(settle))

    report = await run(svc, "BULLISH")

    assert "pd_context" not in report.unreachable


@pytest.mark.asyncio
async def test_a_market_outside_any_range_names_pd_as_unread() -> None:
    """§5.7 needs both anchors to bracket price.

    A ramp that never turns has no range at all -- and that is reported rather
    than scored as though the gate had been checked.
    """
    svc, _ = service(**bullish_setup())

    report = await run(svc, "BULLISH")

    assert "pd_context" in report.unreachable


@pytest.mark.asyncio
async def test_a_swept_mss_origin_zone_at_the_extreme_classifies_as_a1() -> None:
    """§8.6 A1: external sweep -> MSS -> retest of the MSS-origin zone, with
    range-extreme PD and a confirmed stop hunt.

    The last chain to close. It needed three separate things: §5.7's extreme
    third (#57), §5.9's readable history for the retest, and a zone that
    records it came from an MSS -- which nothing wrote, because
    `ict_ob_replay` passed `mss_origin=False` as a literal.
    """
    settle = 990  # range_position 0.2466, inside the lower third

    setup = ranged_setup(settle)
    setup["events"] = [
        event("BOS_UP", 3, direction="UP"),
        event("MSS_UP", 6, direction="UP"),
        event("LIQUIDITY_STOP_HUNT", 7),
    ]
    setup["zones"] = [
        zone(
            "ob",
            grade="OB_A",
            band_low=Decimal(settle - 1),
            band_high=Decimal(settle + 1),
            evidence=json.dumps({"mss_origin": True}),
        )
    ]

    svc, _ = service(**setup, candles=ranged_series(settle), htf_trend="BULLISH")

    report = await run(svc, "BULLISH")

    up = next(c for c in report.candidates if c.direction == "UP")

    assert up.gates_passed
    assert up.archetype == "A1"


@pytest.mark.asyncio
async def test_a_zone_with_no_mss_origin_is_not_the_a1_retest() -> None:
    """§5.1 awards OB_A for an external break *or* an MSS origin, so the grade
    alone cannot say which happened -- the flag has to be read."""
    settle = 990

    setup = ranged_setup(settle)
    setup["events"] = [
        event("BOS_UP", 3, direction="UP"),
        event("MSS_UP", 6, direction="UP"),
        event("LIQUIDITY_STOP_HUNT", 7),
    ]
    setup["zones"] = [
        zone(
            "ob",
            grade="OB_A",
            band_low=Decimal(settle - 1),
            band_high=Decimal(settle + 1),
            evidence=json.dumps({"mss_origin": False}),
        )
    ]

    svc, _ = service(**setup, candles=ranged_series(settle), htf_trend="BULLISH")

    report = await run(svc, "BULLISH")

    assert next(c for c in report.candidates if c.direction == "UP").archetype != "A1"


@pytest.mark.asyncio
async def test_a_clean_record_pays_because_it_is_now_measured() -> None:
    """§3.5's failed break is recorded, so §8.3.1's 15 points can be earned.

    Until the detector existed this term was passed a hardcoded zero, which
    said the market produced no failed break -- a claim nobody had made.
    """
    svc, repo = service(**bullish_setup())

    candidate = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")

    # 15 confirmed + 18 displaced + 10 mss + 15 for a record with no failure
    # in it, and no trend history behind them.
    assert candidate.factors["F1"] == Decimal(58)

    payload = json.loads(next(iter(repo.appended.values())).payload)

    assert "failed_breaks" not in payload["unreachable_inputs"]


@pytest.mark.asyncio
async def test_a_failed_break_against_the_direction_costs_the_clean_record() -> None:
    """§8.3.1: "15 with no failed break against D in 20 candles · 7 with one"."""
    setup = bullish_setup()

    one, _ = service(
        **{**setup, "events": [*setup["events"], failed_break_event(CANDLES - 5, "UP")]}
    )
    clean, _ = service(**setup)

    a = next(c for c in (await run(one, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert b.factors["F1"] - a.factors["F1"] == Decimal(8)


@pytest.mark.asyncio
async def test_two_failures_cost_the_whole_clean_record() -> None:
    setup = bullish_setup()

    two, _ = service(
        **{
            **setup,
            "events": [
                *setup["events"],
                failed_break_event(CANDLES - 5, "UP"),
                failed_break_event(CANDLES - 8, "UP"),
            ],
        }
    )
    clean, _ = service(**setup)

    a = next(c for c in (await run(two, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert b.factors["F1"] - a.factors["F1"] == Decimal(15)


@pytest.mark.asyncio
async def test_an_old_failure_is_outside_the_window() -> None:
    """The window is 20 candles; a failure older than that is not held against D."""
    setup = bullish_setup()

    old, _ = service(
        **{**setup, "events": [*setup["events"], failed_break_event(CANDLES - 25, "UP")]}
    )
    clean, _ = service(**setup)

    a = next(c for c in (await run(old, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F1"] == b.factors["F1"]


@pytest.mark.asyncio
async def test_an_old_failure_stamped_at_the_windows_edge_is_still_old() -> None:
    """The clean-record window was `last_index - failed_index`, two windows apart.

    `last_index` is the right edge of the window being scored. `failed_index`
    is the offset the failure candle had in whichever window recorded it, and
    failures are recorded as they happen -- at the right edge. So the
    difference stays near zero however long ago the failure was, and every
    failure ever recorded counted as inside the last twenty candles.

    Unlike its siblings this one carries **no measurement**: the soak build
    predates §3.5's detector and the VM holds zero of these events, so nothing
    could be counted. The claim rests on the shape of the code, and is worth
    re-checking against real events once the detector is deployed.

    This failure is 25 candles back, outside the window, but stamped with the
    edge index the way a real one is.
    """
    setup = bullish_setup()

    stale_index = failed_break_event(CANDLES - 25, "UP", recorded_index=LAST_INDEX)

    old, _ = service(**{**setup, "events": [*setup["events"], stale_index]})
    clean, _ = service(**setup)

    a = next(c for c in (await run(old, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F1"] == b.factors["F1"]


@pytest.mark.asyncio
async def test_a_failure_the_other_way_is_not_against_this_direction() -> None:
    """A downward break failing does not undermine a long -- if anything it
    supports one, and §8.3.1 asks only about failures against D."""
    setup = bullish_setup()

    other, _ = service(
        **{**setup, "events": [*setup["events"], failed_break_event(CANDLES - 5, "DOWN")]}
    )
    clean, _ = service(**setup)

    a = next(c for c in (await run(other, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F1"] == b.factors["F1"]


@pytest.mark.asyncio
async def test_trend_maturity_is_read_from_the_swing_labels() -> None:
    """§7.4's pairs, from the labels §3.3 already writes into each payload.

    Left at zero, these 30 points could not be earned by any candidate.
    """
    setup = bullish_setup()

    with_trend, _ = service(
        **{
            **setup,
            "events": [
                *setup["events"],
                swing_event(10, "HH"),
                swing_event(11, "HL", kind="LOW"),
                swing_event(12, "HH"),
                swing_event(13, "HL", kind="LOW"),
            ],
        }
    )
    # One labelled swing, so the baseline carries F1's external-break term
    # too and the only thing left between them is trend maturity.
    without, _ = service(**{**setup, "events": [*setup["events"], swing_event(10, "HH")]})

    a = next(c for c in (await run(with_trend, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    # Two pairs of the four §8.3.1 pays in full: 2 / 4 x 30.
    assert a.factors["F1"] - b.factors["F1"] == Decimal(15)


@pytest.mark.asyncio
async def test_a_broken_run_stops_paying_trend_maturity() -> None:
    """One contrary label ends the count -- that is what "unbroken" means."""
    setup = bullish_setup()

    svc, _ = service(
        **{
            **setup,
            "events": [
                *setup["events"],
                swing_event(10, "HH"),
                swing_event(11, "HL", kind="LOW"),
                swing_event(12, "LH"),
            ],
        }
    )
    without, _ = service(**{**setup, "events": [*setup["events"], swing_event(10, "HH")]})

    a = next(c for c in (await run(svc, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    # HH, HL, LH: the pair is there but the run is not, so nothing is paid.
    assert a.factors["F1"] == b.factors["F1"]


@pytest.mark.asyncio
async def test_a_decelerating_market_is_not_paid_as_a_steady_one() -> None:
    """§8.3.1: "accelerating 25 · neither 12 · decelerating 0".

    `momentum_factor` has always read `decelerating`; nothing ever set it, so
    a fading trend collected the 12 points that belong to a steady one.
    """
    setup = bullish_setup()

    fading, _ = service(
        **{
            **setup,
            "zones": [zone("z1", band_low=FADING_LAST_CLOSE - 1, band_high=FADING_LAST_CLOSE + 1)],
        },
        candles=make_series(trend="fading"),
    )
    steady, _ = service(**setup)

    a = next(c for c in (await run(fading, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(steady, "BULLISH")).candidates if c.direction == "UP")

    # Aligned momentum is the only component left standing: no 25 or 12 for
    # acceleration, and no 20 for absent exhaustion -- §7.2 derives
    # `exhaustion_watch` *from* deceleration, so a trend that fades while
    # still grinding out new highs loses both. Asserting the whole factor
    # equals its aligned term says that more exactly than a difference would,
    # since the momentum score moves between the two series as well.
    fading_score = _read_participation(make_series(trend="fading")).score

    assert a.factors["F5"] == fading_score * Decimal("0.55")

    # The steady series keeps the 12 and the 20 that the fading one loses.
    assert b.factors["F5"] - _read_participation(make_series()).score * Decimal("0.55") == 32


def test_a_fading_series_reads_as_decelerating() -> None:
    """The wiring, isolated from what the score then does with it.

    `momentum_factor` has always branched on `decelerating`; `_Reading` never
    carried it, so the branch was dead and every fading market was paid as a
    steady one.
    """
    assert _read_participation(make_series(trend="fading")).decelerating
    assert not _read_participation(make_series()).decelerating


def _expanding(final_close: str) -> list:
    """Twenty flat candles, then three of rising volume with real progress."""
    series = [
        make_candle(
            timeframe=TF,
            open_time=BASE + TF.duration * i,
            open_=Decimal(100),
            close=Decimal(100),
            volume=Decimal(10),
        )
        for i in range(20)
    ]

    for offset, volume in enumerate(("14", "16", "18")):
        index = 20 + offset

        series.append(
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * index,
                open_=Decimal(100),
                close=Decimal(final_close) if offset == 2 else Decimal(100),
                volume=Decimal(volume),
            )
        )

    return series


def test_the_reading_carries_the_expansion_direction() -> None:
    """§6.7's "expansion regime aligned" needs a side to be aligned with.

    §6.3's own progress test is `|Cl[i] - O[i+2]|`, and the sign inside that
    absolute value is the direction. #71 read the discarded sign as an absence
    of one and declared the term unreachable.
    """
    assert _read_participation(_expanding("140")).expansion_direction == "UP"
    assert _read_participation(_expanding("60")).expansion_direction == "DOWN"


def test_a_quiet_market_carries_neither_flag() -> None:
    reading = _read_participation(make_series())

    assert reading.expansion_direction is None
    assert not reading.contracting


def _minute(offset: int, p90: str, *, count: int = 10) -> TradeAggregate:
    """One T4 bucket, `offset` minutes before the newest candle's open."""
    return TradeAggregate(
        symbol="BTCUSDT",
        minute=BASE + TF.duration * (CANDLES - 1) - timedelta(minutes=offset),
        taker_buy_volume=Decimal(5),
        taker_sell_volume=Decimal(5),
        trade_count=count,
        mean_trade_size=Decimal(1),
        stddev_trade_size=Decimal("0.5"),
        p90_trade_size=Decimal(p90),
        max_trade_size=Decimal(p90),
    )


def test_minutes_are_attributed_to_the_candle_they_fell_in() -> None:
    """§6.5 compares this candle's p90 against the trailing twenty candles'.

    The newest candle's own minutes must not leak into the median it is
    measured against, or a big print raises the bar it has to clear.
    """
    series = make_series()

    # H4 candles: minute 0 is inside the newest one, minute 300 is five hours
    # back and so belongs to an earlier candle.
    current, trailing = _size_skew(
        series,
        [_minute(0, "20"), _minute(300, "3"), _minute(600, "5")],
        TF,
    )

    assert current == Decimal(20)
    assert trailing == Decimal(4)


def test_no_coverage_is_no_reading_rather_than_a_zero_one() -> None:
    """§6.5 validates on "aggTrade data fresh"; an uncovered candle has not
    been found to lack big prints, it has not been looked at."""
    assert _size_skew(make_series(), [], TF) == (None, None)


def test_the_reading_carries_the_taker_imbalance() -> None:
    """§6.5(3) is `|delta_pct| >= 0.30`, and nothing was reading it for F4."""
    reading = _read_participation(make_series())

    assert reading.delta is not None


@pytest.mark.asyncio
async def test_a_wash_risk_symbol_has_f4_capped_like_a_suspect_candle() -> None:
    """§6.7: "hard cap 50 if `wash_risk` or `suspect_volume`".

    §6.4's tag is about one candle; §6.6's is about the symbol's whole tape,
    and until now nothing produced it.
    """
    setup = bullish_setup()

    clean, _ = service(**setup, candles=make_series(last_volume="250", last_trades=40))

    tagged_svc, _ = service(
        **setup,
        candles=make_series(last_volume="250", last_trades=40),
        wash_risk=True,
    )

    a = next(c for c in (await run(tagged_svc, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(clean, "BULLISH")).candidates if c.direction == "UP")

    assert b.factors["F4"] == Decimal(65)
    assert a.factors["F4"] == Decimal(50)


@pytest.mark.asyncio
async def test_no_scoring_input_is_structurally_unreadable() -> None:
    """`UNREACHABLE_INPUTS` is the list of terms no engine can supply at all.

    It is empty for the first time. A record can still name `htf_state` or
    `pd_context` -- those are *conditional*: the rung above had no state on
    this pass, or price sat outside a dealing range. That is a fact about the
    market, not about the build, and `_unreachable` adds them per candidate.
    """
    assert UNREACHABLE_INPUTS == ()

    svc, repo = service(**bullish_setup())

    await run(svc, "BULLISH")

    payload = json.loads(next(iter(repo.appended.values())).payload)

    assert "wash_risk" not in payload["unreachable_inputs"]


@pytest.mark.asyncio
async def test_a_break_on_this_candle_is_structural_and_an_older_one_is_not() -> None:
    """§6.5(4)'s break disjunct, which could not distinguish the two.

    `_bos_break_indices` read `break_index` out of the payload -- the break
    candle's offset inside whichever window recorded the event, written once
    and never revised -- and asked whether it equalled `last_index`, the right
    edge of the window being scored. On the VM, 67 of 187 BOS events carry
    `break_index = 500` and `last_index` is 500 on every pass, for both
    scanned symbols, so the disjunct was permanently true. §6.5 exists to say
    that "institutional volume at random locations is not evidence in this
    doctrine", and this made every location structural.

    Nothing in this suite caught it, for the opposite reason: no fixture ever
    put `break_index` in a BOS payload, so `bos_breaks` was empty in every
    test and the disjunct was permanently *false*. The production path and the
    tested path never met.

    `event_at` is the break candle's open time -- `structure_replay` stamps it
    from the same candle it took the index from -- so the same question is
    answerable without an offset, and answerable correctly.
    """
    setup = bullish_setup()

    candles = make_series(last_volume="250", last_trades=40, last_taker_buy="200")
    minutes = [_minute(0, "40")] + [_minute(300 * n, "1") for n in (1, 2, 3)]

    old_break = {**setup, "events": [event("BOS_UP", 3, direction="UP")]}
    break_here = {**setup, "events": [event("BOS_UP", LAST_INDEX, direction="UP")]}

    stale, _ = service(**old_break, candles=candles, minutes=minutes)
    fresh, _ = service(**break_here, candles=candles, minutes=minutes)

    a = next(c for c in (await run(stale, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(fresh, "BULLISH")).candidates if c.direction == "UP")

    # The same 15 points §6.7 pays for institutional volume, on the same tape.
    # Only where the break happened separates them.
    assert a.factors["F4"] == Decimal(65)
    assert b.factors["F4"] == Decimal(80)


@pytest.mark.asyncio
async def test_a_zone_respect_makes_the_candle_a_structural_event() -> None:
    """§6.5(4) lists zone Respect beside displacement, sweeps and breaks.

    It asks whether the *candle* is a structural event candle, so it is any
    zone's Respect -- not the one this setup happens to be scored against,
    which is not even chosen until the gates have passed.
    """
    setup = bullish_setup()

    # Everything §6.5 asks for except a structural candle: abnormal volume,
    # real participation, one-sided tape, and T4 coverage showing this
    # candle's prints far larger than the trailing twenty candles'.
    candles = make_series(last_volume="250", last_trades=40, last_taker_buy="200")
    minutes = [_minute(0, "40")] + [_minute(300 * n, "1") for n in (1, 2, 3)]

    at_random, _ = service(**setup, candles=candles, minutes=minutes)
    at_a_zone, _ = service(**setup, candles=candles, minutes=minutes, respected=True)

    a = next(c for c in (await run(at_random, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(at_a_zone, "BULLISH")).candidates if c.direction == "UP")

    # §6.7 pays 15 for institutional volume on top of the 15 for the aligned
    # spike; without a structural candle §6.5 calls the same tape retail.
    assert a.factors["F4"] == Decimal(65)
    assert b.factors["F4"] == Decimal(80)


class RecordingMetrics:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []

    def observe_pass(self, seconds: float, *, symbol: str, timeframe: str) -> None:
        raise AssertionError("the confluence service does not time passes")

    def record_publication(self, outcome: str, *, timeframe: str) -> None:
        self.outcomes.append((outcome, timeframe))


@pytest.mark.asyncio
async def test_the_funnel_counts_a_publication_a_refresh_and_a_suppression() -> None:
    """§14: the candidate-to-published ratio is "a monitored ratio ... alert on
    ±50% day-over-day shift (doctrine drift detector)".

    Nothing counted it. Three decisions here and each lands under its own
    outcome -- in particular a refresh is neither published nor suppressed,
    because folding it into suppressions would make one setup held for forty
    candles look like forty rejected candidates and move the ratio the alert
    watches.
    """
    metrics = RecordingMetrics()

    svc, _ = service(
        **bullish_setup(),
        signals=FakeSignals(),
        incidents=FakeIncidents(),
        transitions=FakeTransitions(),
        metrics=metrics,
    )

    at = BASE + TF.duration * 10

    await svc._publish("BTCUSDT", TF, at, publishable_candidate())
    await svc._publish("BTCUSDT", TF, at + TF.duration, publishable_candidate())
    await svc._publish(
        "BTCUSDT",
        TF,
        at + TF.duration * 2,
        replace(publishable_candidate(), stale_context=True),
    )

    assert metrics.outcomes == [
        ("published", TF.value),
        ("refreshed", TF.value),
        ("STALE_FEEDS", TF.value),
        ("DUPLICATE_KEY", TF.value),
    ]


@pytest.mark.asyncio
async def test_publishing_without_metrics_still_works() -> None:
    """`NullMetrics` is the default; a collector is never required.

    The golden harness and `engine run` build the service without one, and a
    replay must not increment the live funnel anyway.
    """
    signals = FakeSignals()

    svc, _ = service(**bullish_setup(), signals=signals, incidents=FakeIncidents())

    await svc._publish("BTCUSDT", TF, BASE + TF.duration * 10, publishable_candidate())

    assert len(signals.rows) == 1


class TestTheFreshnessLadderSpeaksBothVocabularies:
    """§8.3.1's state row is written in words from two different enums.

    `FRESH` and `TESTED` are `ZoneState` (OB, breaker, mitigation); `CE_FILLED`
    is `FvgState`. No zone family speaks all three, so without a translation a
    zone is charged for whichever enum its detector happens to use rather than
    for the condition it is in.

    §5.9 supplies the pairing, and does it deliberately: it defines the
    interaction grammar once for "**all** zone objects", so mitigation --
    "price reaches >= 50% of zone depth then closes outside on polarity side"
    -- and an FVG's consequent encroachment are one event under two names.
    """

    def test_mitigated_scores_what_ce_filled_scores(self) -> None:
        """They are the same 50%, and §5.9 counts both as a *Respect* -- which
        is evidence for the zone. Scoring one 6 and the other 0 makes the award
        a fact about the detector rather than about price."""

        from scanner.application.detection.confluence_replay import _FVG_STATE_EQUIVALENT

        assert _FVG_STATE_EQUIVALENT["MITIGATED"] == "CE_FILLED"

    def test_every_translated_word_is_one_the_doctrine_pays_for(self) -> None:
        """A translation to a word outside §8.3.1's table would read as a
        mapping and behave as a deletion -- silently, because an unknown state
        scores zero exactly as an untranslated one does."""

        from scanner.application.detection.confluence_replay import _FVG_STATE_EQUIVALENT
        from scanner.domain.confluence.factor_points import ZONE_STATE_POINTS

        assert set(_FVG_STATE_EQUIVALENT.values()) <= set(ZONE_STATE_POINTS)

    def test_each_zone_family_can_reach_the_ladder(self) -> None:
        """Reachability has to be asked per family, or it cannot fail.

        Asking whether §8.3.1's three words are reachable *at all* is always
        true and always was: `FRESH` and `TESTED` are `ZoneState` members and
        `CE_FILLED` is an `FvgState` one, so the union covers the table however
        the translation is written -- including not written at all.

        The question worth asking is whether a zone of a given family can climb
        the ladder its own detector can express. An FVG that has been touched
        and an order block that has been mitigated are both *somewhere* on it,
        and before the translation each scored zero for being described in the
        wrong dialect.
        """
        from scanner.application.detection.confluence_replay import _FVG_STATE_EQUIVALENT
        from scanner.domain.confluence.factor_points import ZONE_STATE_POINTS
        from scanner.domain.ict.model import FvgState, IfvgState, ZoneState

        def payable(family: type) -> set[str]:
            return {_FVG_STATE_EQUIVALENT.get(state.value, state.value) for state in family} & set(
                ZONE_STATE_POINTS
            )

        # Every family must reach all three rungs. Without the translation
        # `ZoneState` and `IfvgState` reach two: both carry MITIGATED and
        # neither carries CE_FILLED, so the half-consumed award was payable
        # only to FVGs.
        for family in (ZoneState, FvgState, IfvgState):
            assert payable(family) == set(ZONE_STATE_POINTS), (
                f"{family.__name__} reaches {sorted(payable(family))}"
            )


async def test_scoring_pins_its_zone_read_to_the_current_versions() -> None:
    """The 2026-08-29 migration-window lesson, as a wiring contract.

    During a version bump the live table holds both generations of the same
    physical zone. Scoring must ask for the current ones BY NAME -- an
    unpinned read here scored 642 superseded FVGs beside their twins. The
    reader's map must be the writer's map (`CURRENT_ZONE_VERSIONS`), not a
    copy that can drift.
    """
    from scanner.application.detection.zone_versions import CURRENT_ZONE_VERSIONS

    svc, _ = service()

    zones_repo = svc._zones

    await svc.run("BTCUSDT", TF, BASE, END)

    assert zones_repo.asked_versions is CURRENT_ZONE_VERSIONS
