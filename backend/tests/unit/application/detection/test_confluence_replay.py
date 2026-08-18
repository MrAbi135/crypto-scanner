"""Confluence replay: what it reads, what it refuses to guess, what it records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.application.detection.confluence_replay import (
    CONFLUENCE_ALGO_VERSION,
    UNREACHABLE_INPUTS,
    ConfluenceReplayService,
)
from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_evidence import LiquidityEvidenceRecord
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)
TF = Timeframe.H4
END = BASE + TF.duration * 100


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, tzinfo=UTC)


class FakeCandleRepository:
    def __init__(self, count: int) -> None:
        self.series = [
            make_candle(
                timeframe=TF,
                open_time=BASE + TF.duration * i,
                open_=Decimal(100),
                close=Decimal(101),
            )
            for i in range(count)
        ]

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


def zone(
    zone_id: str = "z1",
    *,
    polarity: str = "BULLISH",
    grade: str = "OB_A",
    state: str = "FRESH",
    confirmed_index: int = 5,
) -> IctZoneRecord:
    return IctZoneRecord(
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=TF,
        zone_type="OB",
        polarity=polarity,
        state=state,
        grade=grade,
        band_low=Decimal(100),
        band_high=Decimal(101),
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
        evidence="{}",
    )


# The window is 20 candles, so index 19 is "now". §4.6 gives a sweep 15 closed
# candles of setup relevance, which puts a sweep confirmed at 4 exactly at the
# edge of still counting.
LAST_INDEX = 19


def sweep(
    *,
    side: str = "SSL",
    liquidity_class: str = "EXTERNAL",
    confirmed_index: int = 4,
    expiry_index: int | None = None,
    reclaimed: bool = False,
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
                "sweep_depth_atr": "0.9",
                "reclaimed": reclaimed,
                "setup_expiry_index": (
                    confirmed_index + 15 if expiry_index is None else expiry_index
                ),
            }
        ),
    )


def service(
    *,
    events: list[EngineEventRecord] | None = None,
    zones: list[IctZoneRecord] | None = None,
    liquidity: list[LiquidityEvidenceRecord] | None = None,
    candles: int = 20,
):
    repo = FakeEventRepository(events)

    svc = ConfluenceReplayService(
        FakeCandleRepository(candles),
        repo,
        FakeZoneRepository(zones or []),
        FakeEvidenceRepository(liquidity),
        FakeClock(),
    )

    return svc, repo


async def run(svc, trend_state: str = "RANGING"):
    return await svc.run("BTCUSDT", TF, BASE, END, trend_state=trend_state)


def bullish_setup() -> dict:
    """A context that clears every reachable gate in the UP direction."""
    return {
        "events": [
            event("BOS_UP", 3, direction="UP"),
            event("MSS_UP", 6, direction="UP"),
            event("VOLUME_SPIKE", 7, rvol="3.2"),
            event("MOMENTUM_ACCELERATING", 8, score="70", direction="UP"),
        ],
        "zones": [zone("z1")],
        "liquidity": [sweep()],
    }


@pytest.mark.asyncio
async def test_an_empty_window_grades_nothing() -> None:
    svc, repo = service(candles=0)

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

    assert payload["unreachable_inputs"] == list(UNREACHABLE_INPUTS)
    assert "wash_risk" in payload["unreachable_inputs"]
    assert "target_pool_strength" in payload["unreachable_inputs"]


@pytest.mark.asyncio
async def test_unread_target_pool_terms_do_not_pay_points() -> None:
    """F2's unclaimed/fresh terms describe a pool nothing selects yet.

    Defaulting them True would hand every candidate points for evidence no one
    fetched, and the score would read as earned.
    """
    setup = bullish_setup()

    with_sweep, _ = service(**setup)
    without, _ = service(**{**setup, "liquidity": []})

    # Both graded under a BULLISH state, so the comparison isolates F2 rather
    # than turning into "one of them failed G2".
    a = next(c for c in (await run(with_sweep, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(without, "BULLISH")).candidates if c.direction == "UP")

    # The sweep itself pays; the unread target-pool terms do not, so the score
    # lands short of the F2 ceiling rather than at it.
    assert a.factors["F2"] > b.factors["F2"]
    assert a.factors["F2"] < Decimal(100)


@pytest.mark.asyncio
async def test_volume_reads_the_recorded_rvol_rather_than_a_constant() -> None:
    setup = bullish_setup()

    spiking, _ = service(**setup)
    quiet, _ = service(
        **{**setup, "events": [e for e in setup["events"] if e.event_type != "VOLUME_SPIKE"]}
    )

    a = next(c for c in (await run(spiking)).candidates if c.direction == "UP")
    b = next(c for c in (await run(quiet)).candidates if c.direction == "UP")

    assert a.factors["F4"] > b.factors["F4"]


@pytest.mark.asyncio
async def test_momentum_pointing_the_other_way_is_not_counted_as_support() -> None:
    setup = bullish_setup()

    aligned, _ = service(**setup)
    opposed, _ = service(
        **{
            **setup,
            "events": [e for e in setup["events"] if e.event_type != "MOMENTUM_ACCELERATING"]
            + [event("MOMENTUM_ACCELERATING", 8, score="70", direction="DOWN")],
        }
    )

    a = next(c for c in (await run(aligned)).candidates if c.direction == "UP")
    b = next(c for c in (await run(opposed)).candidates if c.direction == "UP")

    assert a.factors["F5"] > b.factors["F5"]


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
