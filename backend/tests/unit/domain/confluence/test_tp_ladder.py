"""SLS v1.0.15 §15.2's target ladder: `TPn = (start + (n-1) x step) x R`, unbounded.

What is pinned, in the order it could go wrong quietly:

* the rungs land where the owner specified -- TP1 2R, TP2 3R, TP3 4R, and on
  with no last rung;
* the ladder, not the pool, is what §15.3 judges -- through ONE property,
  `exit_target`, so the R:R gate and the order check cannot read different
  targets;
* a non-laddered payload is byte-identical to what it was before the ladder
  existed, because an always-present `"ladder": null` would move the seal of
  every signal the engine publishes;
* the two flip mappings share their keys, because the stop and the ladder were
  measured to work only together.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.confluence import (
    RISK_STOP_PCT,
    TP_LADDER,
    ZONE_DISTAL_EDGE,
    SignalLevels,
    TargetBand,
    TargetLadder,
    entry_zone,
    risk_stop_for,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import SignalPayload
from scanner.shared import Timeframe

LADDER = TargetLadder(start_r=Decimal(2), step_r=Decimal(1))


def entry(direction: str = "UP"):
    """Proximal 104 for a long, 100 for a short."""
    return entry_zone(
        zone_id="z1", direction=direction, band_low=Decimal(100), band_high=Decimal(104)
    )


def laddered(direction: str = "UP", *, pool: str = "120") -> SignalLevels:
    e = entry(direction)

    return SignalLevels(
        direction=direction,
        entry=e,
        invalidation=risk_stop_for(entry=e, direction=direction, stop_pct=Decimal("1.0")),
        primary_target=TargetBand(low=Decimal(pool), high=Decimal(pool), pool_id="p1"),
        ladder=LADDER,
    )


def payload(levels: SignalLevels) -> SignalPayload:
    return SignalPayload(
        symbol="BTCUSDT",
        timeframe="M5",
        direction=levels.direction,
        evidence_ids=("ev-1",),
        confidence=Decimal(72),
        grade="B",
        factors={"F1": "70"},
        archetype="A3",
        reason="r",
        invalidation_distance_atr=Decimal(1),
        invalidation_distance_pct=Decimal(1),
        r_multiple=Decimal(2),
        condition_tags=(),
        levels=levels,
        htf_chain={"H1": "BULLISH"},
        algo_version="s8-test",
        param_set_version="test",
    )


# --- the rungs ---------------------------------------------------------------


def test_the_rungs_are_the_owners_two_three_four() -> None:
    assert [LADDER.rung_r(n) for n in (1, 2, 3)] == [Decimal(2), Decimal(3), Decimal(4)]


def test_the_ladder_has_no_last_rung() -> None:
    """ "TPs ki max koi had na ho" -- no maximum. Rung 52 is as valid as rung 1.

    The one live trade that climbed that far (XRPUSDT M15, 52 rungs) is why
    the rule is stored as start and step rather than as a finite list.
    """
    assert LADDER.rung_r(52) == Decimal(53)


@pytest.mark.parametrize(
    ("start", "step"),
    [
        (Decimal(0), Decimal(1)),
        (Decimal(-1), Decimal(1)),
        (Decimal(2), Decimal(0)),
        (Decimal(2), Decimal(-1)),
    ],
)
def test_a_ladder_that_stalls_or_runs_backwards_is_refused(start: Decimal, step: Decimal) -> None:
    with pytest.raises(ValueError):
        TargetLadder(start_r=start, step_r=step)


def test_rungs_count_from_one() -> None:
    with pytest.raises(ValueError):
        LADDER.rung_r(0)


def test_a_long_ladder_climbs_from_the_proximal_edge_in_r() -> None:
    """Anchor 104, R 1.04 (1% of it): TP1 106.08, TP2 107.12, TP3 108.16."""
    lv = laddered("UP")

    assert [lv.rung_price(n) for n in (1, 2, 3)] == [
        Decimal("106.08"),
        Decimal("107.12"),
        Decimal("108.16"),
    ]


def test_a_short_ladder_descends_from_the_proximal_edge_in_r() -> None:
    """Anchor 100, R 1.00: TP1 98, TP2 97, TP3 96."""
    lv = laddered("DOWN", pool="80")

    assert [lv.rung_price(n) for n in (1, 2, 3)] == [Decimal(98), Decimal(97), Decimal(96)]


def test_a_pool_exit_signal_has_no_rungs() -> None:
    e = entry()
    lv = SignalLevels(
        direction="UP",
        entry=e,
        invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(120), high=Decimal(120)),
    )

    with pytest.raises(ValueError):
        lv.rung_price(1)


# --- what §15.3 judges --------------------------------------------------------


def test_the_exit_target_is_tp1_not_the_pool() -> None:
    lv = laddered("UP", pool="120")

    assert lv.exit_target == lv.rung_price(1) == Decimal("106.08")


def test_the_r_multiple_is_the_ladders_start_by_construction() -> None:
    """Why SLS v1.0.15 records §15.3(3) as satisfied rather than filtering."""
    assert laddered("UP").r_multiple == LADDER.start_r


def test_a_pool_closer_than_the_floor_no_longer_blocks_a_laddered_signal() -> None:
    """The pool is evidence now, not the exit.

    A pool at 105 is under 1R from an anchor of 104 with R = 1.04, which fails
    §15.3(3) under the pool rule. On a ladder TP1 is at 2R regardless, so the
    gate is met -- the measurable difference between "recorded" and "sets the
    exit".
    """
    lv = laddered("UP", pool="105")

    assert lv.meets_rr
    assert lv.coherent


def test_a_pool_exit_signal_is_still_judged_by_its_pool() -> None:
    """The neutrality half: nothing about a non-laddered signal moved."""
    e = entry()
    lv = SignalLevels(
        direction="UP",
        entry=e,
        invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(105), high=Decimal(105)),
    )

    assert lv.exit_target == Decimal(105)
    assert lv.r_multiple == Decimal(3) / Decimal(4), "(105 - 102) / |102 - 98|"
    assert not lv.meets_rr


# --- the seal -----------------------------------------------------------------


def test_a_laddered_payload_seals_the_rule_not_a_list_of_prices() -> None:
    targets = payload(laddered("UP")).as_dict()["targets"]

    assert targets["ladder"] == {"start_r": "2", "step_r": "1", "unbounded": True}
    assert targets["primary"]["pool_id"] == "p1", "the pool is still recorded as evidence"


def test_a_pool_exit_payload_carries_no_ladder_key_at_all() -> None:
    """Not `null` -- absent. An always-present key would change the bytes, the
    seal and the `target_bands` column of every signal, not just laddered ones."""
    e = entry()
    lv = SignalLevels(
        direction="UP",
        entry=e,
        invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(low=Decimal(120), high=Decimal(120)),
    )

    assert "ladder" not in payload(lv).as_dict()["targets"]


# --- the flip -----------------------------------------------------------------


def test_only_m5_has_a_ladder_and_it_is_two_r_in_steps_of_one() -> None:
    """Appendix A v1.0.15: `P.risk.tp_ladder_start` 2R, `tp_ladder_step` 1R,
    M5 only -- TP1 2R, TP2 3R, TP3 4R, with no last rung."""
    assert {Timeframe.M5: TargetLadder(Decimal(2), Decimal(1))} == TP_LADDER


def test_the_stop_and_the_ladder_switch_on_together() -> None:
    """Measured on the 14 published M5 signals: the stop alone is not robust
    and the ladder alone is worse than the rule it replaces; only the pair
    survives. So no timeframe may have one without the other."""
    assert set(TP_LADDER) == set(RISK_STOP_PCT)
