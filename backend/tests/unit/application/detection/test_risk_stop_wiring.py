"""Where SLS v1.0.15's risk stop meets the application: the call site and the monitor.

`RISK_STOP_PCT` ships empty, so in production nothing here fires yet. These
tests switch it on with `monkeypatch.setitem` -- which mutates the one dict
every module imported, and restores it afterwards -- to prove the wiring is
correct before the change that populates it lands. A mechanism that is only
ever exercised after it goes live is a mechanism first tested on real signals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from scanner.application.detection.confluence_replay import _levels_for
from scanner.application.detection.signal_monitor import _levels_of
from scanner.application.ports.signals import SignalRecord
from scanner.domain.confluence import RISK_STOP, RISK_STOP_PCT, ZONE_DISTAL_EDGE, Archetype
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


def test_with_the_mapping_empty_m5_is_untouched() -> None:
    """The neutrality of this change, at the call site: M5 today still gets
    the zone rule, because nothing has switched the risk stop on."""
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


def _record(*, payload: dict) -> SignalRecord:
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
        target_bands=json.dumps({"primary": {"low": "112", "high": "114", "pool_id": "p1"}}),
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
