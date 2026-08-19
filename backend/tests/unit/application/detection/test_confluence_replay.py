"""Confluence replay: what it reads, what it refuses to guess, what it records."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore
from tests.support.builders import make_candle

from scanner.application.detection.confluence_replay import (
    CONFLUENCE_ALGO_VERSION,
    HTF_STATE_UNREACHABLE,
    ConfluenceReplayService,
)
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
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
) -> list:
    """A steadily trending context, with the newest candle's volume settable."""
    out = []

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
            )
        )

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
    zone_type: str = "OB",
    grade: str = "OB_A",
    state: str = "FRESH",
    confirmed_index: int = 5,
    band_low: Decimal | None = None,
    band_high: Decimal | None = None,
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
        evidence="{}",
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
        FakeClock(),
        shift_state,
        shift_algo_version=SHIFT_ALGO,
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
    assert "target_pool_strength" in payload["unreachable_inputs"]

    # Nothing wrote a state for the timeframe above, so F6 was defaulted --
    # and the record says which of its inputs was read and which was not.
    assert HTF_STATE_UNREACHABLE in payload["unreachable_inputs"]


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
async def test_volume_reads_rvol_at_the_newest_candle() -> None:
    """§6.2's RVOL is a per-candle measurement, so F4 reads it at the close.

    Scanning the event log for the last VOLUME_SPIKE answers a different
    question -- "did one ever happen in this window" -- and over a long replay
    the answer is always yes.
    """
    setup = bullish_setup()

    spiking, _ = service(**setup, candles=make_series(last_volume="250"))
    quiet, _ = service(**setup)

    a = next(c for c in (await run(spiking, "BULLISH")).candidates if c.direction == "UP")
    b = next(c for c in (await run(quiet, "BULLISH")).candidates if c.direction == "UP")

    assert a.factors["F4"] > b.factors["F4"]


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
