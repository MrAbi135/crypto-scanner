"""§9.2 over T16's rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.ports.repositories import UniverseStateRecord
from scanner.application.ports.setups import SetupRecord
from scanner.application.ranking import RankingSnapshotService
from scanner.domain.common.universe import UniverseTier
from scanner.shared import Timeframe

AT = datetime(2026, 8, 23, tzinfo=UTC)
TF = Timeframe.H1


class FakeSetups:
    def __init__(self, rows: list[SetupRecord]) -> None:
        self.rows = rows

    async def append(self, setup: SetupRecord) -> bool:
        self.rows.append(setup)

        return True

    async def list_at(self, symbols, timeframe, evaluated_at):
        return tuple(
            r
            for r in self.rows
            if r.symbol in symbols and r.timeframe is timeframe and r.evaluated_at == evaluated_at
        )


class FakeSymbols:
    def __init__(self, tiers: dict[str, UniverseTier]) -> None:
        self.tiers = tiers
        self.asked: list[str] = []

    async def get_universe_state(self, exchange_symbol: str):
        self.asked.append(exchange_symbol)

        tier = self.tiers.get(exchange_symbol)

        if tier is None:
            return None

        return UniverseStateRecord(exchange_symbol=exchange_symbol, tier=tier)


def row(
    symbol: str,
    *,
    direction: str = "UP",
    confidence: str = "80",
    archetype: str | None = "A4",
    floor_passed: bool = True,
    at: datetime = AT,
) -> SetupRecord:
    return SetupRecord(
        setup_id=f"{symbol}-{direction}-{at.isoformat()}",
        symbol=symbol,
        timeframe=TF,
        direction=direction,
        archetype=archetype,
        gate_results=json.dumps({"passed": True, "failed": []}),
        factor_scores=json.dumps({"F1": "70"}),
        adjustments=json.dumps({"applied": [], "synergy": "0", "penalty": "0"}),
        base_confidence=Decimal(confidence),
        final_confidence=Decimal(confidence),
        floor_passed=floor_passed,
        algo_version="test",
        evaluated_at=at,
        evidence=json.dumps({"zone_id": "z"}),
    )


def service(rows, tiers):
    return RankingSnapshotService(FakeSetups(rows), FakeSymbols(tiers))


@pytest.mark.asyncio
async def test_only_published_setups_reach_the_board() -> None:
    """§9.2 ranks "published signals"; §8.6 keeps the rest for calibration.

    The below-floor rows are counted rather than dropped: a board reporting
    only its rows would make a quiet market and a broken pipeline look
    identical, and that distinction is the whole reason to look at one.
    """
    svc = service(
        [
            row("AAAUSDT", confidence="90"),
            row("BBBUSDT", confidence="65", floor_passed=False, archetype=None),
        ],
        {"AAAUSDT": UniverseTier.T1, "BBBUSDT": UniverseTier.T1},
    )

    snapshot = await svc.snapshot(("AAAUSDT", "BBBUSDT"), TF, AT)

    assert [r.setup.symbol for r in snapshot.rows] == ["AAAUSDT"]
    assert snapshot.gate_passers == 2
    assert snapshot.below_floor == 1


@pytest.mark.asyncio
async def test_the_counts_are_of_gate_passers_because_that_is_what_t16_holds() -> None:
    """A sharper number than the one the event log gave.

    This service used to read `SETUP_CANDIDATE_*` events, which include the
    candidates that never cleared §8.2 -- so its "considered" count mixed
    near-misses with contexts that were never setups at all. T16 holds only
    gate-passers, so the counts now say how many real candidates a close
    produced and how many stopped at the floor.
    """
    svc = service([row("AAAUSDT", floor_passed=False, archetype=None)], {})

    snapshot = await svc.snapshot(("AAAUSDT",), TF, AT)

    assert snapshot.rows == ()
    assert snapshot.gate_passers == 1
    assert snapshot.below_floor == 1


@pytest.mark.asyncio
async def test_the_board_is_ordered_and_positions_start_at_one() -> None:
    svc = service(
        [
            row("CCCUSDT", confidence="70"),
            row("AAAUSDT", confidence="90"),
            row("BBBUSDT", confidence="80"),
        ],
        dict.fromkeys(("AAAUSDT", "BBBUSDT", "CCCUSDT"), UniverseTier.T2),
    )

    snapshot = await svc.snapshot(("CCCUSDT", "AAAUSDT", "BBBUSDT"), TF, AT)

    assert [(r.position, r.setup.symbol) for r in snapshot.rows] == [
        (1, "AAAUSDT"),
        (2, "BBBUSDT"),
        (3, "CCCUSDT"),
    ]


@pytest.mark.asyncio
async def test_both_directions_on_one_symbol_are_two_signals() -> None:
    """§9.2 ranks signals, not symbols.

    T16's identity is (symbol, timeframe, direction, close), so a long and a
    short at one close are two rows, and keying the board on the symbol alone
    would have silently dropped one of the pair.
    """
    svc = service(
        [
            row("AAAUSDT", direction="UP", confidence="80"),
            row("AAAUSDT", direction="DOWN", confidence="85"),
        ],
        {"AAAUSDT": UniverseTier.T1},
    )

    snapshot = await svc.snapshot(("AAAUSDT",), TF, AT)

    assert [(r.setup.direction, str(r.setup.confidence)) for r in snapshot.rows] == [
        ("DOWN", "85"),
        ("UP", "80"),
    ]


@pytest.mark.asyncio
async def test_a_symbol_the_registry_does_not_know_ranks_last_not_third() -> None:
    """An unknown tier is not a weak one.

    Defaulting to T3 would rank an unregistered symbol *above* a genuinely
    tier-3 one on §9.2's fourth key, which is worse than admitting ignorance.
    """
    svc = service(
        [row("KNOWNUSDT", confidence="80"), row("UNKNOWNUSDT", confidence="80")],
        {"KNOWNUSDT": UniverseTier.T3},
    )

    snapshot = await svc.snapshot(("KNOWNUSDT", "UNKNOWNUSDT"), TF, AT)

    assert [r.setup.symbol for r in snapshot.rows] == ["KNOWNUSDT", "UNKNOWNUSDT"]
    assert snapshot.rows[1].setup.tier is UniverseTier.INELIGIBLE


@pytest.mark.asyncio
async def test_the_registry_is_asked_once_per_published_symbol() -> None:
    """Two directions on one symbol are one tier lookup, not two.

    Small, but the loop that reads them runs per published row and the
    registry is a database call; asking twice for the same answer is the kind
    of thing that only shows up under a full universe.
    """
    symbols = FakeSymbols({"AAAUSDT": UniverseTier.T1})

    svc = RankingSnapshotService(
        FakeSetups(
            [
                row("AAAUSDT", direction="UP"),
                row("AAAUSDT", direction="DOWN"),
                row("BBBUSDT", floor_passed=False, archetype=None),
            ]
        ),
        symbols,
    )

    await svc.snapshot(("AAAUSDT", "BBBUSDT"), TF, AT)

    # BBBUSDT never published, so its tier is never needed either.
    assert symbols.asked == ["AAAUSDT"]


@pytest.mark.asyncio
async def test_a_published_setup_with_no_archetype_is_refused() -> None:
    """T16's own check constraint refuses this, so it cannot come from the table.

    It can come from a fake or from some future reader of another source, and
    a board should say a record is contradictory rather than invent a rank.
    """
    svc = service([row("AAAUSDT", archetype=None)], {"AAAUSDT": UniverseTier.T1})

    with pytest.raises(ValueError, match="carries no archetype"):
        await svc.snapshot(("AAAUSDT",), TF, AT)


@pytest.mark.asyncio
async def test_the_snapshot_is_one_close_wide() -> None:
    """A setup from the next candle is not on this board."""
    svc = service(
        [
            row("AAAUSDT", confidence="90"),
            row("BBBUSDT", confidence="95", at=AT + TF.duration),
        ],
        dict.fromkeys(("AAAUSDT", "BBBUSDT"), UniverseTier.T1),
    )

    snapshot = await svc.snapshot(("AAAUSDT", "BBBUSDT"), TF, AT)

    assert [r.setup.symbol for r in snapshot.rows] == ["AAAUSDT"]


@pytest.mark.asyncio
async def test_display_decay_is_applied_without_touching_the_confidence() -> None:
    """§9.3 decays the display rank; the recorded confidence is a fact."""
    svc = service([row("AAAUSDT", confidence="90")], {"AAAUSDT": UniverseTier.T1})

    snapshot = await svc.snapshot(("AAAUSDT",), TF, AT, elapsed_candles=12)

    board_row = snapshot.rows[0]

    # H1's TTL is 24, so half of it halves the display rank.
    assert board_row.setup.confidence == Decimal(90)
    assert board_row.display == Decimal(45)
