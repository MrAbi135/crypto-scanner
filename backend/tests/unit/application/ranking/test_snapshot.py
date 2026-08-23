"""§9.2 over what the confluence engine actually recorded."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.repositories import UniverseStateRecord
from scanner.application.ranking import RankingSnapshotService
from scanner.domain.common.universe import UniverseTier
from scanner.shared import Timeframe

AT = datetime(2026, 8, 23, tzinfo=UTC)
TF = Timeframe.H1


class FakeEvents:
    def __init__(self, records: list[EngineEventRecord]) -> None:
        self.records = records

    async def list_events(self, symbol, timeframe, start, end):
        return tuple(
            r
            for r in self.records
            if r.symbol == symbol and r.timeframe is timeframe and start <= r.event_at < end
        )


class FakeSymbols:
    def __init__(self, tiers: dict[str, UniverseTier]) -> None:
        self.tiers = tiers

    async def get_universe_state(self, exchange_symbol: str):
        tier = self.tiers.get(exchange_symbol)

        if tier is None:
            return None

        return UniverseStateRecord(exchange_symbol=exchange_symbol, tier=tier)


def candidate(
    symbol: str,
    *,
    direction: str = "UP",
    confidence: str = "80",
    archetype: str | None = "A4",
    publishable: bool = True,
    at: datetime = AT,
) -> EngineEventRecord:
    return EngineEventRecord(
        event_key=f"{symbol}-{direction}-{at.isoformat()}",
        symbol=symbol,
        timeframe=TF,
        event_type=f"SETUP_CANDIDATE_{direction}",
        event_at=at,
        algo_version="test",
        payload=json.dumps(
            {
                "confidence": confidence,
                "archetype": archetype,
                "publishable": publishable,
                "grade": "A",
            }
        ),
        created_at=at,
    )


def service(records, tiers):
    return RankingSnapshotService(FakeEvents(records), FakeSymbols(tiers))


@pytest.mark.asyncio
async def test_only_published_candidates_reach_the_board() -> None:
    """§9.2 ranks "published signals", and §8.6 records the rest for calibration.

    The below-floor ones are counted rather than discarded: a board reporting
    only its rows makes a quiet market and a broken pipeline look identical,
    and that distinction is the whole reason to look at one.
    """
    svc = service(
        [
            candidate("AAAUSDT", confidence="90"),
            candidate("BBBUSDT", confidence="65", publishable=False, archetype=None),
        ],
        {"AAAUSDT": UniverseTier.T1, "BBBUSDT": UniverseTier.T1},
    )

    snapshot = await svc.snapshot(("AAAUSDT", "BBBUSDT"), TF, AT)

    assert [row.setup.symbol for row in snapshot.rows] == ["AAAUSDT"]
    assert snapshot.considered == 2
    assert snapshot.unpublished == 1


@pytest.mark.asyncio
async def test_the_board_is_ordered_and_positions_start_at_one() -> None:
    svc = service(
        [
            candidate("CCCUSDT", confidence="70"),
            candidate("AAAUSDT", confidence="90"),
            candidate("BBBUSDT", confidence="80"),
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

    A long and a short can be recorded at the same close, and keying the board
    on the symbol alone would have silently dropped one of the pair.
    """
    svc = service(
        [
            candidate("AAAUSDT", direction="UP", confidence="80"),
            candidate("AAAUSDT", direction="DOWN", confidence="85"),
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
        [
            candidate("KNOWNUSDT", confidence="80"),
            candidate("UNKNOWNUSDT", confidence="80"),
        ],
        {"KNOWNUSDT": UniverseTier.T3},
    )

    snapshot = await svc.snapshot(("KNOWNUSDT", "UNKNOWNUSDT"), TF, AT)

    assert [r.setup.symbol for r in snapshot.rows] == ["KNOWNUSDT", "UNKNOWNUSDT"]
    assert snapshot.rows[1].setup.tier is UniverseTier.INELIGIBLE


@pytest.mark.asyncio
async def test_a_publishable_candidate_with_no_archetype_is_refused() -> None:
    """`meets_floor` cannot pass one, so the two cannot both be true.

    If the record says otherwise it is contradictory, and the board should say
    so rather than invent a rank for it.
    """
    svc = service(
        [candidate("AAAUSDT", archetype=None)],
        {"AAAUSDT": UniverseTier.T1},
    )

    with pytest.raises(ValueError, match="carries no archetype"):
        await svc.snapshot(("AAAUSDT",), TF, AT)


@pytest.mark.asyncio
async def test_the_snapshot_is_one_close_wide() -> None:
    """A candidate from the next candle is not on this board."""
    svc = service(
        [
            candidate("AAAUSDT", confidence="90"),
            candidate("BBBUSDT", confidence="95", at=AT + TF.duration),
        ],
        dict.fromkeys(("AAAUSDT", "BBBUSDT"), UniverseTier.T1),
    )

    snapshot = await svc.snapshot(("AAAUSDT", "BBBUSDT"), TF, AT)

    assert [r.setup.symbol for r in snapshot.rows] == ["AAAUSDT"]


@pytest.mark.asyncio
async def test_display_decay_is_applied_without_touching_the_confidence() -> None:
    """§9.3 decays the display rank; the recorded confidence is a fact."""
    svc = service([candidate("AAAUSDT", confidence="90")], {"AAAUSDT": UniverseTier.T1})

    snapshot = await svc.snapshot(("AAAUSDT",), TF, AT, elapsed_candles=12)

    row = snapshot.rows[0]

    # H1's TTL is 24, so half of it halves the display rank.
    assert row.setup.confidence == Decimal(90)
    assert row.display == Decimal(45)
