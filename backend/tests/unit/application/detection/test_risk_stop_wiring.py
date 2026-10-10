"""Where SLS v1.0.15's risk stop meets the application: the call site and the monitor.

These tests were written while `RISK_STOP_PCT` and `TP_LADDER` shipped empty,
and switched M5 on with `monkeypatch.setitem` -- which mutates the one dict
every module imported, and restores it afterwards -- to prove the wiring
before it went live. Since s8-confluence-v36 M5 is on in production, so those
calls are now no-ops that keep each test's premise explicit; the tests for
the live configuration itself sit beside them, and the zone-rule fallback is
exercised by removing M5 with `monkeypatch.delitem`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from scanner.application.detection.confluence_replay import _levels_for
from scanner.application.detection.signal_monitor import _levels_of
from scanner.application.ports.signals import SignalRecord
from scanner.domain.confluence import (
    RISK_STOP,
    RISK_STOP_PCT,
    TP_LADDER,
    ZONE_DISTAL_EDGE,
    Archetype,
    TargetLadder,
)
from scanner.shared import Timeframe


def _zone():
    return SimpleNamespace(
        zone_id="z1",
        band_low=Decimal(100),
        band_high=Decimal(104),
        refined_low=None,
        refined_high=None,
    )


def _pool():
    return SimpleNamespace(
        pool_id="p1", band_low=Decimal(112), band_high=Decimal(114), strength=Decimal(60)
    )


# --- the call site ------------------------------------------------------------


def test_a_timeframe_with_a_stop_pct_gets_a_risk_stop_whatever_the_archetype(monkeypatch) -> None:
    """The decisive case: an A1 with no recorded swept extreme.

    Under the archetype rule that A1 has no invalidation at all and cannot
    publish. Under a risk stop the archetype has no say -- the stop is the
    risk budget, not the thesis -- so it gets one.
    """
    monkeypatch.setitem(RISK_STOP_PCT, Timeframe.M5, Decimal("1.0"))

    levels, unmet = _levels_for(
        Archetype.SWEEP_REVERSAL,
        timeframe=Timeframe.M5,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert unmet == ()
    assert levels is not None
    assert levels.invalidation.rule == RISK_STOP
    assert levels.invalidation.price == Decimal("102.96")


def test_a_timeframe_without_one_keeps_the_archetype_rule(monkeypatch) -> None:
    """M5 switched on must not leak onto H1."""
    monkeypatch.setitem(RISK_STOP_PCT, Timeframe.M5, Decimal("1.0"))

    levels, _ = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.H1,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert levels is not None
    assert levels.invalidation.rule == ZONE_DISTAL_EDGE
    assert levels.invalidation.price == Decimal(100)


def test_the_live_m5_configuration_is_a_risk_stop_and_a_ladder() -> None:
    """No monkeypatch: what production publishes on M5 since v36."""
    levels, unmet = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.M5,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert unmet == ()
    assert levels is not None
    assert levels.invalidation.rule == RISK_STOP
    assert levels.invalidation.price == Decimal("102.96")
    assert levels.ladder == TargetLadder(Decimal(2), Decimal(1))
    assert levels.rung_price(1) == Decimal("106.08")


def test_the_live_configuration_leaves_h1_on_its_pool() -> None:
    levels, _ = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.H1,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert levels is not None
    assert levels.invalidation.rule == ZONE_DISTAL_EDGE
    assert levels.ladder is None
    assert levels.exit_target == Decimal(112)


def test_with_m5_removed_from_the_mapping_m5_falls_back_to_the_zone_rule(monkeypatch) -> None:
    """The mechanism, not the setting: a timeframe absent from the mapping
    gets the archetype's rule -- which is every timeframe but M5."""
    monkeypatch.delitem(RISK_STOP_PCT, Timeframe.M5)
    monkeypatch.delitem(TP_LADDER, Timeframe.M5)

    levels, _ = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.M5,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert levels is not None
    assert levels.invalidation.rule == ZONE_DISTAL_EDGE


# --- the monitor --------------------------------------------------------------


def _record(*, payload: dict, ladder: dict | None = None) -> SignalRecord:
    return SignalRecord(
        signal_id="sig-1",
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        direction="UP",
        archetype="A3",
        grade="B",
        final_confidence=Decimal(72),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal("102.96"),
        target_bands=json.dumps(
            {"primary": {"low": "112", "high": "114", "pool_id": "p1"}}
            | ({"ladder": ladder} if ladder is not None else {})
        ),
        published_at=datetime(2026, 10, 10, tzinfo=UTC),
        ttl_candles=24,
        algo_version="s8-test",
        param_set_version="test",
        payload=json.dumps(payload),
        payload_hash="h",
        dedup_key="d",
    )


def test_the_monitor_keeps_the_rule_the_signal_was_sealed_with() -> None:
    """It used to rebuild every invalidation with rule "" -- harmless while
    every rule anchored at the mid, and a silent wrong unit for every §12.4
    excursion once a risk stop anchors at the proximal edge."""
    levels = _levels_of(_record(payload={"invalidation": {"rule": RISK_STOP, "price": "102.96"}}))

    assert levels.invalidation.rule == RISK_STOP
    assert levels.anchor == Decimal(104), "the monitor measured R from the mid"
    assert levels.r_unit == Decimal("1.04")


def test_a_payload_without_the_field_reconstructs_to_the_mid() -> None:
    """A signal sealed before the rule was recorded keeps the origin it had."""
    levels = _levels_of(_record(payload={}))

    assert levels.invalidation.rule == ""
    assert levels.anchor == Decimal(102)


def test_the_monitor_reads_the_seal_not_todays_setting(monkeypatch) -> None:
    """Switching the setting on later must not relabel a signal sealed under
    the zone rule -- that is the retroactive relabel the seal prevents."""
    monkeypatch.setitem(RISK_STOP_PCT, Timeframe.M5, Decimal("1.0"))

    levels = _levels_of(
        _record(payload={"invalidation": {"rule": ZONE_DISTAL_EDGE, "price": "100"}})
    )

    assert levels.invalidation.rule == ZONE_DISTAL_EDGE
    assert levels.anchor == Decimal(102)


# --- the ladder (SLS v1.0.15 part 2) -----------------------------------------


def test_a_laddered_timeframe_exits_on_tp1_at_two_r(monkeypatch) -> None:
    """Both mappings on together, as the flip will do: the exit is the ladder,
    the pool stays as evidence, and the R-multiple is the ladder's start."""
    monkeypatch.setitem(RISK_STOP_PCT, Timeframe.M5, Decimal("1.0"))
    monkeypatch.setitem(TP_LADDER, Timeframe.M5, TargetLadder(Decimal(2), Decimal(1)))

    levels, unmet = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.M5,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert unmet == ()
    assert levels is not None
    assert levels.ladder == TargetLadder(Decimal(2), Decimal(1))
    assert levels.r_multiple == Decimal(2)
    assert levels.primary_target.pool_id == "p1", "the pool must still be recorded"


def test_a_laddered_timeframe_still_needs_a_pool(monkeypatch) -> None:
    """§15.2 says the bands "are still recorded". Without one the signal does
    not publish -- so a laddered timeframe publishes exactly the set the old
    rule did, which is the set the M5 measurement was taken on."""
    monkeypatch.setitem(RISK_STOP_PCT, Timeframe.M5, Decimal("1.0"))
    monkeypatch.setitem(TP_LADDER, Timeframe.M5, TargetLadder(Decimal(2), Decimal(1)))

    levels, unmet = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.M5,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=None,
        pd=None,
    )

    assert levels is None
    assert unmet == ("primary_target",)


def test_a_ladder_on_m5_does_not_leak_onto_h1(monkeypatch) -> None:
    monkeypatch.setitem(TP_LADDER, Timeframe.M5, TargetLadder(Decimal(2), Decimal(1)))

    levels, _ = _levels_for(
        Archetype.CONTINUATION_PULLBACK,
        timeframe=Timeframe.H1,
        direction="UP",
        zone=_zone(),
        swept_extreme=None,
        target_pool=_pool(),
        pd=None,
    )

    assert levels is not None
    assert levels.ladder is None


def test_the_monitor_rebuilds_the_ladder_the_signal_was_sealed_with() -> None:
    levels = _levels_of(
        _record(
            payload={"invalidation": {"rule": RISK_STOP, "price": "102.96"}},
            ladder={"start_r": "2", "step_r": "1", "unbounded": True},
        )
    )

    assert levels.ladder == TargetLadder(Decimal(2), Decimal(1))
    assert levels.rung_price(1) == Decimal("106.08")


def test_a_signal_sealed_without_a_ladder_exits_on_its_pool() -> None:
    """Every signal published before SLS v1.0.15 -- and every non-M5 one after."""
    levels = _levels_of(_record(payload={}))

    assert levels.ladder is None
    assert levels.exit_target == Decimal(112)


def test_the_monitor_reads_the_ladder_from_the_seal_not_todays_setting(monkeypatch) -> None:
    """A signal sealed with no ladder keeps exiting on its pool even after M5
    is switched on -- the setting must never relabel a sealed signal."""
    monkeypatch.setitem(TP_LADDER, Timeframe.M5, TargetLadder(Decimal(2), Decimal(1)))

    levels = _levels_of(_record(payload={}))

    assert levels.ladder is None
