"""The API's Target Zone row: a ladder arrives priced, a pool exit arrives as stored."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from scanner.application.ports.signals import SignalRecord
from scanner.domain.confluence import RISK_STOP
from scanner.interfaces.api.targets import RUNGS_PRICED, targets_view
from scanner.shared import Timeframe

POOL = {"low": "120", "high": "120", "pool_id": "p1"}


def record(*, ladder: dict | None, rule: str = RISK_STOP, invalidation: str = "102.96"):
    bands = {"primary": POOL, "secondary": None} | ({"ladder": ladder} if ladder else {})

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
        invalidation_level=Decimal(invalidation),
        target_bands=json.dumps(bands),
        published_at=datetime(2026, 10, 10, tzinfo=UTC),
        ttl_candles=48,
        algo_version="s8-test",
        param_set_version="test",
        payload=json.dumps({"invalidation": {"rule": rule, "price": invalidation}}),
        payload_hash="h",
        dedup_key="d",
    )


def test_a_laddered_signal_arrives_with_its_first_rungs_priced() -> None:
    """Anchor 104, R 1.04: TP1 106.08, TP2 107.12, TP3 108.16 -- the owner's
    2%, 3%, 4% on a 1% stop."""
    view = targets_view(record(ladder={"start_r": "2", "step_r": "1", "unbounded": True}))

    rungs = view["ladder"]["rungs"]

    assert [(r["n"], r["r"], r["price"]) for r in rungs] == [
        (1, "2", "106.08"),
        (2, "3", "107.12"),
        (3, "4", "108.16"),
    ]
    assert len(rungs) == RUNGS_PRICED
    assert view["ladder"]["unbounded"] is True, "the rungs shown are not the last ones"
    assert "trails" in view["ladder"]["trailing"]
    assert view["primary"] == POOL, "the pool is still shown, as evidence"


def test_the_prices_come_from_the_seal_not_from_the_stored_invalidation_alone() -> None:
    """R is anchored by the sealed rule. Read as a zone stop, the same record
    would measure R from the mid (102) and price TP1 at 103.92 instead."""
    zone_rule = targets_view(
        record(
            ladder={"start_r": "2", "step_r": "1", "unbounded": True},
            rule="zone_distal_edge",
            invalidation="100",
        )
    )

    assert zone_rule["ladder"]["rungs"][0]["price"] == "106", "(mid 102) + 2 x |102 - 100|"


def test_a_pool_exit_signal_is_returned_exactly_as_stored() -> None:
    """Nothing added: every response that predates the ladder is unchanged."""
    stored = record(ladder=None)

    assert targets_view(stored) == json.loads(stored.target_bands)
