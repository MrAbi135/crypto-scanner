"""Integration: S4/S5/S6 detection repositories vs real TimescaleDB.

These repositories push doctrine invariants down into SQL, where no unit fake
can reach them:

* **State resurrection is refused by the database.** `PgIctZoneRepository.upsert`
  carries `WHERE NOT state IN (terminal…)` on its ON CONFLICT clause, and the
  liquidity pool upsert carries `WHERE state = 'ACTIVE'`. A terminal zone or a
  swept pool silently ignores further writes. SLS §4/§5 call this permanent;
  only a live Postgres proves it.
* **Transitions are optimistically concurrent.** `transition()` matches on the
  expected `from_state` and reports `rowcount`, so a caller holding a stale read
  gets `False` rather than clobbering a newer state.
* **Append is idempotent.** Every event/transition/interaction writer uses
  ON CONFLICT DO NOTHING keyed on its natural id, so replay cannot double-write.
* **Decimals survive storage exactly.** Bands are `numeric(38,18)`; the
  Constitution's no-float law is only worth anything if the round-trip is exact.
* **Ordering is deterministic.** Every list query carries a total sort, because
  detection output feeds a ranking that must not depend on row order.

Requires Docker (testcontainers). Run: pytest -m integration tests/integration
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_zone_interactions import IctZoneInteractionRecord
from scanner.application.ports.ict_zones import IctZoneRecord, IctZoneTransitionRecord
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.application.ports.param_sets import ParamSetRecord
from scanner.domain.ict import MAX_ZONES
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.detection_repositories import (
    PgEngineEventRepository,
)
from scanner.infrastructure.persistence.ict_evidence_repository import (
    PgIctEvidenceRepository,
)
from scanner.infrastructure.persistence.ict_zone_interaction_repository import (
    PgIctZoneInteractionContextRepository,
    PgIctZoneInteractionRepository,
)
from scanner.infrastructure.persistence.ict_zone_repositories import (
    PgIctZoneRepository,
    PgIctZoneTransitionRepository,
)
from scanner.infrastructure.persistence.liquidity_detection_repositories import (
    PgLiquidityPoolRepository,
    PgLiquidityTransitionRepository,
)
from scanner.infrastructure.persistence.param_set_repository import PgParamSetRepository
from scanner.shared import Timeframe

pytestmark = pytest.mark.integration

TF = Timeframe.M5
T0 = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)

# Terminal zone states, mirroring _TERMINAL_STATES in the repository.
TERMINAL_ZONE_STATES = ("INVALIDATED", "EXPIRED", "FILLED", "INVERTED", "DEAD")

# An 18-decimal-place value: exercises the full numeric(38,18) scale.
EXACT = Decimal("101.123456789012345678")


def zone(
    zone_id: str,
    *,
    symbol: str,
    state: str = "FRESH",
    zone_type: str = "OB",
    grade: str = "OB_A",
    band_low: Decimal = Decimal("100"),
    band_high: Decimal = Decimal("102"),
    created_index: int = 10,
    created_at: datetime = T0,
    evidence: str = '{"v":1}',
) -> IctZoneRecord:
    return IctZoneRecord(
        zone_id=zone_id,
        symbol=symbol,
        timeframe=TF,
        zone_type=zone_type,
        polarity="BULLISH",
        state=state,
        grade=grade,
        band_low=band_low,
        band_high=band_high,
        refined_low=Decimal("100.5"),
        refined_high=Decimal("101.5"),
        created_index=created_index,
        confirmed_index=created_index + 1,
        created_at=created_at,
        updated_at=created_at,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=True,
        evidence=evidence,
    )


def pool(
    pool_id: str,
    *,
    symbol: str,
    state: str = "ACTIVE",
    strength: Decimal = Decimal("50"),
    price: Decimal = Decimal("100"),
    evidence: str = '{"v":1}',
) -> LiquidityPoolRecord:
    return LiquidityPoolRecord(
        pool_id=pool_id,
        symbol=symbol,
        timeframe=TF,
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=price,
        band_low=price,
        band_high=price,
        strength=strength,
        state=state,
        member_count=1,
        created_index=5,
        created_at=T0,
        updated_at=T0,
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# PgIctZoneRepository
# --------------------------------------------------------------------------


async def test_zone_roundtrip_preserves_decimal_scale_exactly(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))

    await repo.upsert(
        zone("z-exact", symbol="ZEXACT", band_low=EXACT, band_high=EXACT + Decimal("1"))
    )

    stored = await repo.get("z-exact")

    assert stored is not None
    assert stored.band_low == EXACT
    assert stored.band_high == EXACT + Decimal("1")
    assert stored.timeframe is TF


async def test_zone_get_returns_none_for_unknown_id(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))

    assert await repo.get("no-such-zone") is None


async def test_zone_upsert_updates_a_live_zone(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))
    await repo.upsert(zone("z-live", symbol="ZLIVE"))

    await repo.upsert(
        zone(
            "z-live",
            symbol="ZLIVE",
            grade="OB_B",
            band_high=Decimal("105"),
            evidence='{"v":2}',
        )
    )

    stored = await repo.get("z-live")

    assert stored is not None
    assert stored.grade == "OB_B"
    assert stored.band_high == Decimal("105")
    assert stored.evidence == '{"v":2}'


@pytest.mark.parametrize("terminal", TERMINAL_ZONE_STATES)
async def test_terminal_zone_cannot_be_resurrected_by_upsert(
    engine,
    terminal: str,
) -> None:
    """SLS §5 state-resurrection prohibition, enforced in the ON CONFLICT WHERE."""

    repo = PgIctZoneRepository(build_session_factory(engine))
    zone_id = f"z-term-{terminal}"
    symbol = f"ZTERM{terminal}"

    await repo.upsert(zone(zone_id, symbol=symbol))
    assert await repo.transition(
        zone_id,
        from_state="FRESH",
        to_state=terminal,
        updated_at=T0 + timedelta(minutes=5),
    )

    # A later engine pass tries to write the zone again.
    await repo.upsert(
        zone(
            zone_id,
            symbol=symbol,
            grade="OB_B",
            band_high=Decimal("999"),
            evidence='{"resurrected":true}',
        )
    )

    stored = await repo.get(zone_id)

    assert stored is not None
    assert stored.state == terminal
    assert stored.grade == "OB_A"
    assert stored.band_high == Decimal("102")
    assert stored.evidence == '{"v":1}'


async def test_zone_transition_is_optimistically_concurrent(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))
    await repo.upsert(zone("z-cc", symbol="ZCC"))

    first = await repo.transition("z-cc", from_state="FRESH", to_state="TESTED", updated_at=T0)
    # A caller holding the stale FRESH read loses the race and is told so.
    stale = await repo.transition("z-cc", from_state="FRESH", to_state="MITIGATED", updated_at=T0)

    assert first is True
    assert stale is False

    stored = await repo.get("z-cc")
    assert stored is not None
    assert stored.state == "TESTED"


async def test_zone_transition_on_unknown_zone_reports_false(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))

    assert not await repo.transition("nope", from_state="FRESH", to_state="TESTED", updated_at=T0)


async def test_list_live_excludes_terminal_zones_and_sorts_deterministically(
    engine,
) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))
    symbol = "ZLIST"

    await repo.upsert(zone("z-a", symbol=symbol, created_at=T0, zone_type="OB"))
    await repo.upsert(
        zone("z-b", symbol=symbol, created_at=T0 + timedelta(hours=2), zone_type="FVG")
    )
    await repo.upsert(
        zone("z-c", symbol=symbol, created_at=T0 + timedelta(hours=2), zone_type="BPR")
    )
    await repo.upsert(zone("z-dead", symbol=symbol, created_at=T0 + timedelta(hours=9)))
    assert await repo.transition(
        "z-dead", from_state="FRESH", to_state="INVALIDATED", updated_at=T0
    )

    live = await repo.list_live(symbol, TF)

    # created_at DESC, then zone_type ASC, then zone_id ASC.
    assert [z.zone_id for z in live] == ["z-c", "z-b", "z-a"]
    assert all(z.state not in TERMINAL_ZONE_STATES for z in live)


async def test_list_live_orders_by_real_time_not_the_window_offset(engine) -> None:
    """`created_index` is the offset inside the window that detected the zone.

    Two zones found in different windows carry offsets that cannot be compared
    -- the window slides, and the offset is frozen at detection. Ordering the
    live set by it sorted zones by an accident of when the engine happened to
    look, and once §5.1's bound truncates that order it stops being cosmetic:
    it decides which zones §8 is allowed to see.

    Here the older zone carries the *higher* offset, which is the ordinary
    case for anything detected near a window's right edge and then left behind.
    """
    repo = PgIctZoneRepository(build_session_factory(engine))
    symbol = "ZORDER"

    await repo.upsert(
        zone("z-old", symbol=symbol, created_index=499, created_at=T0, zone_type="OB")
    )
    await repo.upsert(
        zone(
            "z-new",
            symbol=symbol,
            created_index=12,
            created_at=T0 + timedelta(days=1),
            zone_type="OB",
        )
    )

    live = await repo.list_live(symbol, TF)

    assert [z.zone_id for z in live] == ["z-new", "z-old"]


async def test_list_live_is_bounded_at_max_zones(engine) -> None:
    """§5.1: "zone set bounded at P.ict.max_zones = 60 per symbol-TF".

    Nothing bounded it. On the soak VM one symbol-TF carried 9,463 live zones
    against a stated 60, and every confluence pass read all of them to pick a
    best zone -- so G4 was choosing from a set 158 times larger than the
    doctrine allows it to consider.

    The newest survive, because §5.1 says to evict the oldest.
    """
    repo = PgIctZoneRepository(build_session_factory(engine))
    symbol = "ZBOUND"

    for i in range(MAX_ZONES + 15):
        await repo.upsert(
            zone(
                f"z-{i:03d}",
                symbol=symbol,
                created_at=T0 + timedelta(hours=i),
                zone_type="OB",
            )
        )

    live = await repo.list_live(symbol, TF)

    assert len(live) == MAX_ZONES

    # The fifteen oldest are the ones gone.
    assert live[0].zone_id == f"z-{MAX_ZONES + 14:03d}"
    assert live[-1].zone_id == "z-015"


async def test_list_live_is_scoped_to_symbol_and_timeframe(engine) -> None:
    repo = PgIctZoneRepository(build_session_factory(engine))
    await repo.upsert(zone("z-scope", symbol="ZSCOPE"))

    assert len(await repo.list_live("ZSCOPE", TF)) == 1
    assert await repo.list_live("ZSCOPE", Timeframe.H1) == ()
    assert await repo.list_live("OTHER", TF) == ()


# --------------------------------------------------------------------------
# Storage tripwires (DDD §18)
# --------------------------------------------------------------------------


async def test_check_constraint_rejects_an_inverted_zone_band(engine) -> None:
    async with engine.connect() as conn:
        with pytest.raises(Exception, match="ck_ict_zones_band"):
            await conn.execute(
                text(
                    "INSERT INTO detection.ict_zones (zone_id, symbol, timeframe, "
                    "zone_type, polarity, state, grade, band_low, band_high, "
                    "created_index, confirmed_index, created_at, updated_at, "
                    "stale_context, gap_adjacent, evidence) VALUES "
                    "('bad-band','XX','M5','OB','BULLISH','FRESH','OB_A', 200, 100, "
                    "1, 2, now(), now(), false, false, '{}')"
                )
            )
        await conn.rollback()


async def test_check_constraint_rejects_a_half_populated_refined_band(engine) -> None:
    async with engine.connect() as conn:
        with pytest.raises(Exception, match="ck_ict_zones_refined_band"):
            await conn.execute(
                text(
                    "INSERT INTO detection.ict_zones (zone_id, symbol, timeframe, "
                    "zone_type, polarity, state, grade, band_low, band_high, "
                    "refined_low, created_index, confirmed_index, created_at, "
                    "updated_at, stale_context, gap_adjacent, evidence) VALUES "
                    "('bad-refined','XX','M5','OB','BULLISH','FRESH','OB_A', 100, 200, "
                    "150, 1, 2, now(), now(), false, false, '{}')"
                )
            )
        await conn.rollback()


# --------------------------------------------------------------------------
# PgIctZoneTransitionRepository / interactions / engine events — idempotency
# --------------------------------------------------------------------------


def transition_record(transition_id: str, *, zone_id: str) -> IctZoneTransitionRecord:
    return IctZoneTransitionRecord(
        transition_id=transition_id,
        zone_id=zone_id,
        symbol="ZTR",
        timeframe=TF,
        zone_type="OB",
        from_state="FRESH",
        to_state="TESTED",
        reason="close_confirmed",
        transitioned_at=T0,
        candle_index=11,
        evidence='{"v":1}',
    )


async def test_zone_transition_append_is_idempotent(engine) -> None:
    repo = PgIctZoneTransitionRepository(build_session_factory(engine))
    zones = PgIctZoneRepository(build_session_factory(engine))
    await zones.upsert(zone("z-tr", symbol="ZTR"))

    record = transition_record("tr-1", zone_id="z-tr")

    assert await repo.append(record) is True
    assert await repo.append(record) is False  # replay does not double-write


async def test_zone_interaction_append_is_idempotent(engine) -> None:
    zones = PgIctZoneRepository(build_session_factory(engine))
    await zones.upsert(zone("z-int", symbol="ZINT"))

    repo = PgIctZoneInteractionRepository(build_session_factory(engine))
    record = IctZoneInteractionRecord(
        interaction_id="int-1",
        zone_id="z-int",
        symbol="ZINT",
        timeframe=TF,
        zone_type="OB",
        kind="REJECTION",
        observed_at=T0,
        candle_index=12,
        penetration_depth=Decimal("0.5"),
        close_price=EXACT,
        rejection_wick=Decimal("1.25"),
        close_through=False,
        evidence='{"v":1}',
    )

    assert await repo.append(record) is True
    assert await repo.append(record) is False


async def test_engine_event_append_is_idempotent_and_queryable(engine) -> None:
    repo = PgEngineEventRepository(build_session_factory(engine))
    record = EngineEventRecord(
        event_key="ev-1",
        symbol="ZEV",
        timeframe=TF,
        event_type="SWING_CONFIRMED",
        event_at=T0,
        algo_version="1.0.0",
        payload='{"v":1}',
        created_at=T0,
    )

    assert await repo.exists("ev-1") is False
    assert await repo.append(record) is True
    assert await repo.append(record) is False
    assert await repo.exists("ev-1") is True


# --------------------------------------------------------------------------
# PgIctZoneInteractionContextRepository
# --------------------------------------------------------------------------


async def test_context_reader_skips_zones_that_can_no_longer_interact(engine) -> None:
    """§5 makes terminal states permanent -- "no resurrection".

    This reader used to return every zone for the context, which had the
    interaction replay walk 3,934 zones on real BTCUSDT H1 where only 701 could
    still do anything. The cost was five sixths of the largest service's work,
    spent on zones that were dead.

    What that gives up: on an empty database, a terminal zone's historical
    interactions are no longer re-derived. Nothing reads them -- confluence only
    ever asks `list_for_zone` about the zone G4 has put price at, which is live
    by definition -- so the trade was taken deliberately rather than silently.
    """

    sessions = build_session_factory(engine)
    zones = PgIctZoneRepository(sessions)
    symbol = "ZCTX"

    await zones.upsert(zone("c-1", symbol=symbol, created_index=1, zone_type="OB"))
    await zones.upsert(zone("c-2", symbol=symbol, created_index=2, zone_type="FVG"))
    assert await zones.transition("c-2", from_state="FRESH", to_state="EXPIRED", updated_at=T0)

    context = PgIctZoneInteractionContextRepository(sessions)
    listed = await context.list_zones(symbol, TF)

    assert [z.zone_id for z in listed] == ["c-1"]
    assert [z.state for z in listed] == ["FRESH"]


async def test_a_mitigated_zone_is_not_terminal_and_is_still_read(engine) -> None:
    """MITIGATED is in neither §5 terminal set, and the filter must respect that.

    A zone that has been mitigated can still be tested again; excluding it
    would drop live evidence rather than dead weight.
    """

    sessions = build_session_factory(engine)
    zones = PgIctZoneRepository(sessions)
    symbol = "ZMIT"

    await zones.upsert(zone("m-1", symbol=symbol, created_index=1, zone_type="OB"))
    assert await zones.transition("m-1", from_state="FRESH", to_state="MITIGATED", updated_at=T0)

    listed = await PgIctZoneInteractionContextRepository(sessions).list_zones(symbol, TF)

    assert [z.zone_id for z in listed] == ["m-1"]


async def test_context_reader_orders_transitions_by_candle_index(engine) -> None:
    sessions = build_session_factory(engine)
    await PgIctZoneRepository(sessions).upsert(zone("c-tr", symbol="ZCTXTR"))

    appender = PgIctZoneTransitionRepository(sessions)
    # Appended out of order on purpose: the reader must impose the ordering.
    for transition_id, candle_index in (("t-late", 30), ("t-early", 10), ("t-mid", 20)):
        await appender.append(
            IctZoneTransitionRecord(
                transition_id=transition_id,
                zone_id="c-tr",
                symbol="ZCTXTR",
                timeframe=TF,
                zone_type="OB",
                from_state="FRESH",
                to_state="TESTED",
                reason="close_confirmed",
                transitioned_at=T0,
                candle_index=candle_index,
                evidence='{"v":1}',
            )
        )

    context = PgIctZoneInteractionContextRepository(sessions)
    listed = await context.list_transitions("c-tr")

    assert [t.candle_index for t in listed] == [10, 20, 30]
    assert [t.transition_id for t in listed] == ["t-early", "t-mid", "t-late"]


# --------------------------------------------------------------------------
# PgLiquidityPoolRepository
# --------------------------------------------------------------------------


async def test_pool_roundtrip_and_unknown_id(engine) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))

    await repo.upsert(pool("p-1", symbol="PONE", price=EXACT, strength=Decimal("77.5")))
    stored = await repo.get("p-1")

    assert stored is not None
    assert stored.price == EXACT
    assert stored.strength == Decimal("77.5")
    assert await repo.get("missing") is None


async def test_active_pool_upsert_updates_in_place(engine) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))
    await repo.upsert(pool("p-live", symbol="PLIVE"))

    await repo.upsert(pool("p-live", symbol="PLIVE", strength=Decimal("90"), evidence='{"v":2}'))

    stored = await repo.get("p-live")
    assert stored is not None
    assert stored.strength == Decimal("90")
    assert stored.evidence == '{"v":2}'


@pytest.mark.parametrize("terminal", ["SWEPT", "BROKEN", "EXPIRED"])
async def test_terminal_pool_cannot_be_revived_by_upsert(
    engine,
    terminal: str,
) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))
    pool_id = f"p-term-{terminal}"
    symbol = f"PTERM{terminal}"

    await repo.upsert(pool(pool_id, symbol=symbol))
    assert await repo.transition(pool_id, to_state=terminal, updated_at=T0)

    await repo.upsert(
        pool(pool_id, symbol=symbol, strength=Decimal("99"), evidence='{"revived":true}')
    )

    stored = await repo.get(pool_id)
    assert stored is not None
    assert stored.state == terminal
    assert stored.strength == Decimal("50")
    assert stored.evidence == '{"v":1}'


async def test_pool_transition_is_one_way(engine) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))
    await repo.upsert(pool("p-once", symbol="PONCE"))

    assert await repo.transition("p-once", to_state="SWEPT", updated_at=T0) is True
    # Already terminal: the WHERE state='ACTIVE' guard refuses a second move.
    assert await repo.transition("p-once", to_state="BROKEN", updated_at=T0) is False

    stored = await repo.get("p-once")
    assert stored is not None
    assert stored.state == "SWEPT"


@pytest.mark.parametrize("target", ["ACTIVE", "FRESH", "TESTED", ""])
async def test_pool_transition_target_must_be_terminal(engine, target: str) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))

    with pytest.raises(ValueError, match="must be terminal"):
        await repo.transition("p-any", to_state=target, updated_at=T0)


async def test_list_active_sorts_by_strength_then_price_then_id(engine) -> None:
    repo = PgLiquidityPoolRepository(build_session_factory(engine))
    symbol = "PLIST"

    await repo.upsert(pool("p-w", symbol=symbol, strength=Decimal("10"), price=Decimal("1")))
    await repo.upsert(pool("p-x", symbol=symbol, strength=Decimal("90"), price=Decimal("5")))
    await repo.upsert(pool("p-y", symbol=symbol, strength=Decimal("90"), price=Decimal("2")))
    await repo.upsert(pool("p-z", symbol=symbol, strength=Decimal("50"), price=Decimal("3")))
    await repo.upsert(pool("p-gone", symbol=symbol, strength=Decimal("99")))
    assert await repo.transition("p-gone", to_state="SWEPT", updated_at=T0)

    active = await repo.list_active(symbol, TF)

    assert [p.pool_id for p in active] == ["p-y", "p-x", "p-z", "p-w"]
    assert all(p.state == "ACTIVE" for p in active)


async def test_liquidity_transition_append_is_idempotent(engine) -> None:
    sessions = build_session_factory(engine)
    await PgLiquidityPoolRepository(sessions).upsert(pool("p-tr", symbol="PTR"))

    repo = PgLiquidityTransitionRepository(sessions)
    record = LiquidityTransitionRecord(
        transition_id="ltr-1",
        pool_id="p-tr",
        symbol="PTR",
        timeframe=TF,
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="sweep_confirmed",
        transitioned_at=T0,
        candle_index=7,
        evidence='{"v":1}',
    )

    assert await repo.append(record) is True
    assert await repo.append(record) is False


# --------------------------------------------------------------------------
# PgIctEvidenceRepository — the S6 engine reading S4/S5 facts
# --------------------------------------------------------------------------


async def test_evidence_reader_filters_structure_events_by_type_and_window(
    engine,
) -> None:
    sessions = build_session_factory(engine)
    events = PgEngineEventRepository(sessions)
    symbol = "ZEVID"

    async def add(key: str, event_type: str, minutes: int) -> None:
        await events.append(
            EngineEventRecord(
                event_key=key,
                symbol=symbol,
                timeframe=TF,
                event_type=event_type,
                event_at=T0 + timedelta(minutes=minutes),
                algo_version="1.0.0",
                payload=f'{{"k":"{key}"}}',
                created_at=T0,
            )
        )

    await add("e-swing", "SWING_CONFIRMED", 10)
    await add("e-struct", "STRUCTURE_BOS", 20)
    await add("e-other", "LIQUIDITY_POOL_CREATED", 30)  # wrong type
    await add("e-outside", "SWING_CONFIRMED", 500)  # outside the window

    reader = PgIctEvidenceRepository(sessions)
    found = await reader.list_structure(symbol, TF, T0, T0 + timedelta(minutes=100))

    assert [r.event_type for r in found] == ["SWING_CONFIRMED", "STRUCTURE_BOS"]
    assert all(r.algo_version == "1.0.0" for r in found)


async def test_evidence_reader_returns_liquidity_transitions_in_candle_order(
    engine,
) -> None:
    sessions = build_session_factory(engine)
    symbol = "PEVID"
    await PgLiquidityPoolRepository(sessions).upsert(pool("p-ev", symbol=symbol))

    appender = PgLiquidityTransitionRepository(sessions)
    for transition_id, candle_index, minutes in (
        ("lt-b", 40, 40),
        ("lt-a", 20, 20),
        ("lt-out", 60, 5000),
    ):
        await appender.append(
            LiquidityTransitionRecord(
                transition_id=transition_id,
                pool_id="p-ev",
                symbol=symbol,
                timeframe=TF,
                from_state="ACTIVE",
                to_state="SWEPT",
                reason="sweep_confirmed",
                transitioned_at=T0 + timedelta(minutes=minutes),
                candle_index=candle_index,
                evidence='{"v":1}',
            )
        )

    reader = PgIctEvidenceRepository(sessions)
    found = await reader.list_liquidity(symbol, TF, T0, T0 + timedelta(minutes=100))

    assert [r.candle_index for r in found] == [20, 40]
    assert all(r.pool_id == "p-ev" for r in found)


async def test_evidence_reader_is_empty_for_an_unknown_symbol(engine) -> None:
    reader = PgIctEvidenceRepository(build_session_factory(engine))

    assert await reader.list_structure("NOPE", TF, T0, T0 + timedelta(days=1)) == ()
    assert await reader.list_liquidity("NOPE", TF, T0, T0 + timedelta(days=1)) == ()


# --------------------------------------------------------------------------
# T10 parameter sets (DDD T10, TAD §14)
# --------------------------------------------------------------------------


async def test_param_set_round_trips_and_a_repeat_registration_is_ignored(engine) -> None:
    """T10 is written per release and read at boot.

    Registration is `ON CONFLICT DO NOTHING` because two engine processes
    booting together both see no row and both register. They register the same
    triple with the same digest, so the loser of the race has nothing to
    correct -- but an upsert would let a second process silently rewrite the
    first's payload, which is the one thing a version registry must not allow.
    """
    repo = PgParamSetRepository(build_session_factory(engine))

    record = ParamSetRecord(
        engine="detection",
        algo_version="s8-pgtest",
        param_set_version="2026.08.24.1",
        param_payload='{"parameters":[]}',
        checksum="a" * 64,
        sls_reference="Appendix A",
        deployed_at=T0,
    )

    await repo.register(record)
    await repo.register(replace(record, checksum="b" * 64))

    found = await repo.get("detection", "s8-pgtest", "2026.08.24.1")

    assert found is not None
    assert found.checksum == "a" * 64
    assert found.param_payload == '{"parameters":[]}'


async def test_a_row_with_no_checksum_reads_as_absent(engine) -> None:
    """Migration 013 backfilled the existing rows rather than inventing digests.

    A null checksum is the absence of a record, not a record of absence. If
    `get` returned such a row, boot would compare the running digest against
    `None`, call it a mismatch, and refuse to start on rows that only ever
    meant "this predates verification".
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO detection.algo_versions "
                "(id, engine, version, param_set_version, created_at) "
                "VALUES ('legacy-row','detection','s4-old','unverified', now())"
            )
        )

    repo = PgParamSetRepository(build_session_factory(engine))

    assert await repo.get("detection", "s4-old", "unverified") is None


async def test_the_same_algo_version_may_carry_two_parameter_sets(engine) -> None:
    """The key widened for exactly this.

    A parameter change keeps the algo version and increments
    `param_set_version` (SLS Appendix A), so the old `(engine, version)`
    unique constraint would have rejected the second row -- the registry would
    have been unable to record the very event it exists to track.
    """
    repo = PgParamSetRepository(build_session_factory(engine))

    for version, digest in (("2026.01.01.1", "c" * 64), ("2026.02.01.1", "d" * 64)):
        await repo.register(
            ParamSetRecord(
                engine="detection",
                algo_version="s8-twoparams",
                param_set_version=version,
                param_payload="{}",
                checksum=digest,
                sls_reference=None,
                deployed_at=T0,
            )
        )

    first = await repo.get("detection", "s8-twoparams", "2026.01.01.1")
    second = await repo.get("detection", "s8-twoparams", "2026.02.01.1")

    assert first is not None and first.checksum == "c" * 64
    assert second is not None and second.checksum == "d" * 64


async def test_the_rebuilt_interaction_table_kept_its_shape(engine) -> None:
    """Migration 011 drops the table and builds a new one in its place.

    `CREATE TABLE ... (LIKE ... INCLUDING CONSTRAINTS)` carries the columns,
    the NOT NULLs and the kind check; the indexes are created explicitly
    afterwards so they keep their canonical names. Neither half is obvious
    from reading the migration, and a rebuild that quietly dropped a NOT NULL
    or renamed an index would pass every test that only inserts valid rows.

    The original migration deleted in place instead. Planned against the soak
    VM it came out as two sequential scans of 24.7 million rows with two
    sorts, and a DELETE reclaims nothing -- the 15 GB table would have kept
    15 GB of dead tuples on a host with 23 GB free.
    """
    async with engine.connect() as conn:
        indexes = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname='detection' "
                        "AND tablename='ict_zone_interactions'"
                    )
                )
            ).all()
        }

        not_null = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='detection' "
                        "AND table_name='ict_zone_interactions' "
                        "AND is_nullable='NO'"
                    )
                )
            ).all()
        }

        checks = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid='detection.ict_zone_interactions'::regclass "
                        "AND contype='c'"
                    )
                )
            ).all()
        }

    assert indexes == {
        "pk_ict_zone_interactions",
        "ix_ict_zone_interactions_context_time",
        "ix_ict_zone_interactions_zone_time",
        "uq_ict_zone_interactions_identity",
    }

    # Every column: the table has no nullable ones, and a rebuild is exactly
    # where that would be lost.
    assert not_null == {
        "interaction_id",
        "zone_id",
        "symbol",
        "timeframe",
        "zone_type",
        "kind",
        "observed_at",
        "candle_index",
        "penetration_depth",
        "close_price",
        "rejection_wick",
        "close_through",
        "evidence",
    }

    assert "ck_ict_zone_interactions_kind" in checks
