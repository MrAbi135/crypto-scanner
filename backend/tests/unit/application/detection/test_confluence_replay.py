"""Confluence replay: what it reads, what it refuses to guess, what it records."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore
from tests.support.builders import make_candle

from scanner.application.detection.confluence_replay import (
    CONFLUENCE_ALGO_VERSION,
    HTF_STATE_UNREACHABLE,
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
from scanner.domain.common import TradeAggregate
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

    async def list_live(self, symbol, timeframe):
        return tuple(self.zones)


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

    def __init__(self, items: dict[str, list] | None = None) -> None:
        self.items = items or {}

    async def append(self, interaction) -> bool:
        return True

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


def failed_break_event(index: int, direction: str) -> EngineEventRecord:
    """§3.5's failed break, as `structure_replay` writes it."""
    at = BASE + TF.duration * index

    return EngineEventRecord(
        event_key=f"STRUCTURE_FAILED_BREAK_{direction}-{index}",
        symbol="BTCUSDT",
        timeframe=TF,
        event_type=f"STRUCTURE_FAILED_BREAK_{direction}",
        event_at=at,
        algo_version="test",
        payload=json.dumps({"failed": True, "failed_index": index, "direction": direction}),
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
    band_low: Decimal | None = None,
    band_high: Decimal | None = None,
    evidence: str = "{}",
) -> IctZoneRecord:
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
        created_at=BASE,
        updated_at=BASE,
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
    expiry_index: int | None = None,
    reclaimed: bool = False,
    depth_atr: str = "0.9",
) -> LiquidityEvidenceRecord:
    return LiquidityEvidenceRecord(
        pool_id="p1",
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="liquidity_sweep",
        transitioned_at=BASE + TF.duration * confirmed_index,
        candle_index=confirmed_index,
        evidence=json.dumps(
            {
                "side": side,
                "liquidity_class": liquidity_class,
                "sweep_depth_atr": depth_atr,
                "reclaimed": reclaimed,
                "setup_expiry_index": (
                    confirmed_index + 15 if expiry_index is None else expiry_index
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
    pools: list | None = None,
    minutes: list | None = None,
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
        FakeInteractions(interactions),
        FakePools(pools),
        FakeTradeAggregates(minutes),
        FakeClock(),
        shift_state,
        shift_algo_version=SHIFT_ALGO,
    )

    return svc, repo


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

    The liquidity engine stamps `setup_expiry_index` on every sweep. Nothing was
    reading it, so a sweep from the far end of an 1849-candle replay counted as
    present evidence of current flow.
    """
    setup = bullish_setup()
    setup["liquidity"] = [sweep(confirmed_index=1, expiry_index=LAST_INDEX - 1)]
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
    honestly a month later."""
    svc, repo = service(**bullish_setup())

    await run(svc)

    payload = json.loads(next(iter(repo.appended.values())).payload)

    assert "wash_risk" in payload["unreachable_inputs"]

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

    expired, _ = service(**{**setup, "liquidity": [sweep(expiry_index=0)]})
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
