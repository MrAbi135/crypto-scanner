"""SLS §5.1-§5.3 branch coverage: OB detection guards and zone lifecycles.

`test_fvg_ob_pd_ote.py` covers the OB happy path. This module covers the
rejection branches of `detect_order_block`, the breaker/mitigation promotion
guards, and the close-confirmed state machines that all three zone types share
(FRESH → TESTED → MITIGATED, plus INVALIDATED / EXPIRED terminals).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.ict.breakers import BreakerBlock, advance_breaker, create_breaker
from scanner.domain.ict.displacement import Displacement, DisplacementDirection
from scanner.domain.ict.mitigation import (
    MitigationBlock,
    advance_mitigation_block,
    create_mitigation_block,
)
from scanner.domain.ict.model import ZoneBand, ZonePolarity, ZoneState
from scanner.domain.ict.order_blocks import (
    OrderBlock,
    advance_order_block,
    detect_order_block,
)
from scanner.shared import Timeframe

BASE_TIME = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
ATR = Decimal("2")

# Shared geometry for lifecycle tests: band 100-102, refined 100.5-101.5 (mid 101).
BAND = ZoneBand(low=Decimal("100"), high=Decimal("102"))
REFINED = ZoneBand(low=Decimal("100.5"), high=Decimal("101.5"))


def candle_at(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=BASE_TIME + timedelta(minutes=5 * index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.STREAM,
    )


def displacement_at(
    index: int,
    direction: DisplacementDirection = DisplacementDirection.BULLISH,
) -> Displacement:
    return Displacement(
        candle_index=index,
        direction=direction,
        body=Decimal("4"),
        candle_range=Decimal("5"),
        mean_body_20=Decimal("1"),
        atr=ATR,
        body_multiple=Decimal("4"),
        range_multiple=Decimal("2.5"),
        close_position=Decimal("0.1"),
    )


def build_series(overrides: dict[int, Candle], *, length: int = 30) -> list[Candle]:
    """Neutral filler series with specific indices replaced."""

    series = [
        overrides.get(
            index,
            candle_at(index, open_="100", high="100.4", low="99.6", close="100.1"),
        )
        for index in range(length)
    ]
    return series


BEARISH_RUN = {
    20: candle_at(20, open_="105", high="105.5", low="103.5", close="104"),
    21: candle_at(21, open_="104", high="104.5", low="102.5", close="103"),
    22: candle_at(22, open_="103", high="103.5", low="101.5", close="102"),
}


def make_ob(
    *,
    polarity: ZonePolarity = ZonePolarity.BULLISH,
    state: ZoneState = ZoneState.FRESH,
    origin_swept: bool = True,
    origin_failure_swing: bool = False,
    confirmed_index: int = 23,
) -> OrderBlock:
    return OrderBlock(
        ob_id="ob-1",
        polarity=polarity,
        band=BAND,
        refined_band=REFINED,
        created_index=20,
        confirmed_index=confirmed_index,
        created_at=BASE_TIME,
        grade="OB_A",
        origin_swept=origin_swept,
        origin_failure_swing=origin_failure_swing,
        stale_context=False,
        state=state,
    )


def make_breaker(
    *,
    polarity: ZonePolarity = ZonePolarity.BULLISH,
    state: ZoneState = ZoneState.FRESH,
) -> BreakerBlock:
    return BreakerBlock(
        breaker_id="brk-1",
        parent_ob_id="ob-1",
        polarity=polarity,
        band=BAND,
        refined_band=REFINED,
        created_index=23,
        created_at=BASE_TIME,
        grade="BRK_A",
        gap_break=False,
        state=state,
    )


def make_mitigation(
    *,
    polarity: ZonePolarity = ZonePolarity.BULLISH,
    state: ZoneState = ZoneState.FRESH,
) -> MitigationBlock:
    return MitigationBlock(
        mitigation_id="mit-1",
        parent_ob_id="ob-1",
        polarity=polarity,
        band=BAND,
        refined_band=REFINED,
        created_index=23,
        created_at=BASE_TIME,
        state=state,
    )


# --------------------------------------------------------------------------
# detect_order_block — rejection branches
# --------------------------------------------------------------------------


def detect(
    candles: Sequence[Candle] | None = None,
    **overrides: object,
) -> OrderBlock | None:
    kwargs: dict[str, object] = {
        "candidate_end_index": 22,
        "displacement": displacement_at(23),
        "atr": ATR,
        "external_structure_break": True,
        "internal_structure_break": False,
        "mss_origin": False,
        "fvg_created": False,
        "origin_swept": False,
        "origin_failure_swing": False,
    }
    kwargs.update(overrides)

    return detect_order_block(
        candles if candles is not None else build_series(BEARISH_RUN),
        **kwargs,  # type: ignore[arg-type]
    )


def test_order_block_detection_baseline_confirms() -> None:
    ob = detect()

    assert ob is not None
    assert ob.polarity is ZonePolarity.BULLISH
    assert ob.band == ZoneBand(low=Decimal("101.5"), high=Decimal("105.5"))
    assert ob.refined_band == ZoneBand(low=Decimal("102"), high=Decimal("105"))
    assert ob.created_index == 20
    assert ob.confirmed_index == 23
    assert ob.grade == "OB_A"


@pytest.mark.parametrize("atr", [Decimal("0"), Decimal("-1")])
def test_order_block_requires_positive_atr(atr: Decimal) -> None:
    with pytest.raises(ValueError, match="atr must be positive"):
        detect(atr=atr)


@pytest.mark.parametrize("candidate_end_index", [-1, 30, 99])
def test_order_block_rejects_out_of_range_candidate(candidate_end_index: int) -> None:
    assert detect(candidate_end_index=candidate_end_index) is None


def test_order_block_requires_displacement_after_the_candidate() -> None:
    assert detect(displacement=displacement_at(22)) is None
    assert detect(displacement=displacement_at(21)) is None


def test_order_block_rejects_displacement_beyond_the_five_candle_window() -> None:
    assert detect(displacement=displacement_at(28)) is None


def test_order_block_requires_a_structure_break_or_fvg() -> None:
    assert (
        detect(
            external_structure_break=False,
            internal_structure_break=False,
            fvg_created=False,
        )
        is None
    )


def test_order_block_accepts_an_fvg_only_qualification_as_grade_b() -> None:
    ob = detect(
        external_structure_break=False,
        internal_structure_break=False,
        fvg_created=True,
    )

    assert ob is not None
    assert ob.grade == "OB_B"


def test_order_block_grade_a_can_come_from_mss_origin() -> None:
    ob = detect(
        external_structure_break=False,
        internal_structure_break=True,
        mss_origin=True,
    )

    assert ob is not None
    assert ob.grade == "OB_A"


def test_order_block_rejects_a_band_thinner_than_the_atr_floor() -> None:
    """Band height 0.2 against ATR 2 is below the 0.15 * ATR = 0.3 floor."""

    thin_run = {
        22: candle_at(22, open_="103", high="103.1", low="102.9", close="102.95"),
    }

    assert detect(build_series(thin_run), atr=ATR) is None


def test_order_block_rejects_a_band_wider_than_the_atr_ceiling() -> None:
    """Band height 4 against ATR 1 exceeds the 3 * ATR = 3 ceiling."""

    assert detect(atr=Decimal("1")) is None


def test_order_block_rejects_an_all_doji_candidate_run() -> None:
    doji_run = {
        20: candle_at(20, open_="103", high="104", low="102", close="103"),
        21: candle_at(21, open_="103", high="104", low="102", close="103"),
        22: candle_at(22, open_="103", high="104", low="102", close="103"),
    }

    assert detect(build_series(doji_run)) is None


def test_order_block_rejects_a_non_opposing_candidate_candle() -> None:
    """A bullish candle cannot open a bullish OB's opposing run."""

    bullish_run = {
        22: candle_at(22, open_="102", high="103.5", low="101.5", close="103"),
    }

    assert detect(build_series(bullish_run)) is None


def test_order_block_run_absorbs_a_doji_before_a_real_body() -> None:
    """A doji is absorbed into the run; a non-opposing candle ends it.

    Index 22 is a doji and index 21 is bearish, so both join the bullish OB's
    opposing run. Index 20 is left as the bullish filler candle, which is not
    opposing and therefore terminates the scan — the run starts at 21.
    """

    mixed_run = {
        21: candle_at(21, open_="104", high="104.5", low="102.5", close="103"),
        22: candle_at(22, open_="103", high="103.5", low="101.5", close="103"),
    }

    ob = detect(build_series(mixed_run))

    assert ob is not None
    assert ob.created_index == 21
    assert ob.band == ZoneBand(low=Decimal("101.5"), high=Decimal("104.5"))


def test_bearish_order_block_forms_from_a_bullish_run() -> None:
    bullish_run = {
        20: candle_at(20, open_="100", high="101.5", low="99.5", close="101"),
        21: candle_at(21, open_="101", high="102.5", low="100.5", close="102"),
        22: candle_at(22, open_="102", high="103.5", low="101.5", close="103"),
    }

    ob = detect(
        build_series(bullish_run),
        displacement=displacement_at(23, DisplacementDirection.BEARISH),
    )

    assert ob is not None
    assert ob.polarity is ZonePolarity.BEARISH
    assert ob.band == ZoneBand(low=Decimal("99.5"), high=Decimal("103.5"))


# --------------------------------------------------------------------------
# advance_order_block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ZoneState.INVALIDATED, ZoneState.EXPIRED])
def test_terminal_order_block_cannot_transition(state: ZoneState) -> None:
    with pytest.raises(ValueError, match="terminal OB cannot transition"):
        advance_order_block(
            make_ob(state=state),
            candle_at(24, open_="101", high="102", low="100", close="101"),
            candle_index=24,
        )


def test_fresh_order_block_expires_past_the_age_cap() -> None:
    ob = make_ob()

    aged = advance_order_block(
        ob,
        candle_at(300, open_="101", high="102", low="100", close="101"),
        candle_index=ob.confirmed_index + 251,
    )

    assert aged.state is ZoneState.EXPIRED


def test_tested_order_block_does_not_expire_on_age_alone() -> None:
    """The age cap only retires zones that were never touched."""

    ob = make_ob(state=ZoneState.TESTED)

    aged = advance_order_block(
        ob,
        candle_at(300, open_="103", high="104", low="103", close="103.5"),
        candle_index=ob.confirmed_index + 251,
    )

    assert aged.state is ZoneState.TESTED


def test_bullish_order_block_is_invalidated_by_a_close_below_the_band() -> None:
    advanced = advance_order_block(
        make_ob(),
        candle_at(24, open_="100", high="100.5", low="98", close="99"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_bearish_order_block_is_invalidated_by_a_close_above_the_band() -> None:
    advanced = advance_order_block(
        make_ob(polarity=ZonePolarity.BEARISH),
        candle_at(24, open_="102", high="104", low="101.5", close="103"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_untouched_order_block_is_unchanged() -> None:
    ob = make_ob()

    advanced = advance_order_block(
        ob,
        candle_at(24, open_="105", high="107", low="104", close="106"),
        candle_index=24,
    )

    assert advanced is ob
    assert advanced.state is ZoneState.FRESH


def test_bullish_order_block_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_order_block(
        make_ob(),
        candle_at(24, open_="101", high="103", low="100.5", close="102.5"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bullish_order_block_touched_above_the_midpoint_is_only_tested() -> None:
    advanced = advance_order_block(
        make_ob(),
        candle_at(24, open_="102", high="103", low="101.6", close="102.5"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.TESTED


def test_bearish_order_block_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_order_block(
        make_ob(polarity=ZonePolarity.BEARISH),
        candle_at(24, open_="100.5", high="101.5", low="99", close="99.5"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bearish_order_block_touched_below_the_midpoint_is_only_tested() -> None:
    advanced = advance_order_block(
        make_ob(polarity=ZonePolarity.BEARISH),
        candle_at(24, open_="100.2", high="100.4", low="99", close="99.5"),
        candle_index=24,
    )

    assert advanced.state is ZoneState.TESTED


def test_tested_order_block_touched_again_without_mitigation_is_unchanged() -> None:
    ob = make_ob(state=ZoneState.TESTED)

    advanced = advance_order_block(
        ob,
        candle_at(24, open_="102", high="103", low="101.6", close="102.5"),
        candle_index=24,
    )

    assert advanced is ob


# --------------------------------------------------------------------------
# create_breaker
# --------------------------------------------------------------------------


def promote_breaker(**overrides: object) -> BreakerBlock | None:
    kwargs: dict[str, object] = {
        "invalidation_index": 30,
        "invalidation_at": BASE_TIME,
        "displacement": displacement_at(30, DisplacementDirection.BEARISH),
        "structure_break": True,
    }
    kwargs.update(overrides)

    ob = kwargs.pop("ob", None) or make_ob(state=ZoneState.INVALIDATED)

    return create_breaker(ob, **kwargs)  # type: ignore[arg-type]


def test_breaker_promotion_flips_polarity_and_inherits_bands() -> None:
    breaker = promote_breaker()

    assert breaker is not None
    assert breaker.polarity is ZonePolarity.BEARISH
    assert breaker.parent_ob_id == "ob-1"
    assert breaker.band == BAND
    assert breaker.refined_band == REFINED
    assert breaker.grade == "BRK_A"
    assert breaker.state is ZoneState.FRESH
    assert breaker.gap_break is False


def test_breaker_records_a_gap_break_flag() -> None:
    breaker = promote_breaker(gap_break=True)

    assert breaker is not None
    assert breaker.gap_break is True


def test_breaker_id_is_deterministic() -> None:
    assert promote_breaker().breaker_id == promote_breaker().breaker_id  # type: ignore[union-attr]


def test_breaker_requires_an_invalidated_parent() -> None:
    with pytest.raises(ValueError, match="breaker requires INVALIDATED parent OB"):
        promote_breaker(ob=make_ob(state=ZoneState.FRESH))


def test_breaker_requires_a_swept_origin() -> None:
    ob = make_ob(state=ZoneState.INVALIDATED, origin_swept=False)

    assert promote_breaker(ob=ob) is None


def test_breaker_requires_a_structure_break() -> None:
    assert promote_breaker(structure_break=False) is None


def test_breaker_requires_displacement_opposing_the_parent_polarity() -> None:
    assert promote_breaker(displacement=displacement_at(30)) is None


def test_breaker_requires_displacement_on_the_invalidation_candle() -> None:
    assert (
        promote_breaker(
            displacement=displacement_at(31, DisplacementDirection.BEARISH),
        )
        is None
    )


def test_bearish_parent_promotes_to_a_bullish_breaker() -> None:
    ob = make_ob(polarity=ZonePolarity.BEARISH, state=ZoneState.INVALIDATED)

    breaker = promote_breaker(ob=ob, displacement=displacement_at(30))

    assert breaker is not None
    assert breaker.polarity is ZonePolarity.BULLISH


# --------------------------------------------------------------------------
# advance_breaker
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ZoneState.INVALIDATED, ZoneState.EXPIRED])
def test_terminal_breaker_cannot_transition(state: ZoneState) -> None:
    with pytest.raises(ValueError, match="terminal breaker cannot transition"):
        advance_breaker(
            make_breaker(state=state),
            candle_at(31, open_="101", high="102", low="100", close="101"),
        )


def test_bullish_breaker_is_invalidated_below_the_band() -> None:
    advanced = advance_breaker(
        make_breaker(),
        candle_at(31, open_="100", high="100.5", low="98", close="99"),
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_bearish_breaker_is_invalidated_above_the_band() -> None:
    advanced = advance_breaker(
        make_breaker(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="102", high="104", low="101.5", close="103"),
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_untouched_breaker_is_unchanged() -> None:
    breaker = make_breaker()

    advanced = advance_breaker(
        breaker,
        candle_at(31, open_="105", high="107", low="104", close="106"),
    )

    assert advanced is breaker


def test_bullish_breaker_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_breaker(
        make_breaker(),
        candle_at(31, open_="101", high="103", low="100.5", close="102.5"),
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bullish_breaker_touched_above_the_midpoint_is_only_tested() -> None:
    advanced = advance_breaker(
        make_breaker(),
        candle_at(31, open_="102", high="103", low="101.6", close="102.5"),
    )

    assert advanced.state is ZoneState.TESTED


def test_bearish_breaker_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_breaker(
        make_breaker(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="100.5", high="101.5", low="99", close="99.5"),
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bearish_breaker_touched_below_the_midpoint_is_only_tested() -> None:
    advanced = advance_breaker(
        make_breaker(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="100.2", high="100.4", low="99", close="99.5"),
    )

    assert advanced.state is ZoneState.TESTED


def test_tested_breaker_touched_again_without_mitigation_is_unchanged() -> None:
    breaker = make_breaker(state=ZoneState.TESTED)

    advanced = advance_breaker(
        breaker,
        candle_at(31, open_="102", high="103", low="101.6", close="102.5"),
    )

    assert advanced is breaker


# --------------------------------------------------------------------------
# create_mitigation_block
# --------------------------------------------------------------------------


def promote_mitigation(**overrides: object) -> MitigationBlock | None:
    kwargs: dict[str, object] = {
        "invalidation_index": 30,
        "invalidation_at": BASE_TIME,
        "displacement": displacement_at(30, DisplacementDirection.BEARISH),
        "structure_break": True,
    }
    kwargs.update(overrides)

    ob = kwargs.pop("ob", None) or make_ob(
        state=ZoneState.INVALIDATED,
        origin_swept=False,
        origin_failure_swing=True,
    )

    return create_mitigation_block(ob, **kwargs)  # type: ignore[arg-type]


def test_mitigation_promotion_flips_polarity_and_inherits_bands() -> None:
    block = promote_mitigation()

    assert block is not None
    assert block.polarity is ZonePolarity.BEARISH
    assert block.parent_ob_id == "ob-1"
    assert block.band == BAND
    assert block.refined_band == REFINED
    assert block.grade == "MIT"
    assert block.state is ZoneState.FRESH


def test_mitigation_id_is_deterministic() -> None:
    assert promote_mitigation().mitigation_id == promote_mitigation().mitigation_id  # type: ignore[union-attr]


def test_mitigation_requires_an_invalidated_parent() -> None:
    with pytest.raises(ValueError, match="mitigation requires INVALIDATED parent OB"):
        promote_mitigation(ob=make_ob(state=ZoneState.FRESH, origin_swept=False))


def test_mitigation_is_refused_when_the_origin_was_swept() -> None:
    """A swept origin belongs to the breaker path, not the mitigation path."""

    ob = make_ob(
        state=ZoneState.INVALIDATED,
        origin_swept=True,
        origin_failure_swing=True,
    )

    assert promote_mitigation(ob=ob) is None


def test_mitigation_requires_an_origin_failure_swing() -> None:
    ob = make_ob(
        state=ZoneState.INVALIDATED,
        origin_swept=False,
        origin_failure_swing=False,
    )

    assert promote_mitigation(ob=ob) is None


def test_mitigation_requires_a_structure_break() -> None:
    assert promote_mitigation(structure_break=False) is None


def test_mitigation_requires_displacement_opposing_the_parent_polarity() -> None:
    assert promote_mitigation(displacement=displacement_at(30)) is None


def test_mitigation_requires_displacement_on_the_invalidation_candle() -> None:
    assert (
        promote_mitigation(
            displacement=displacement_at(31, DisplacementDirection.BEARISH),
        )
        is None
    )


def test_bearish_parent_promotes_to_a_bullish_mitigation_block() -> None:
    ob = make_ob(
        polarity=ZonePolarity.BEARISH,
        state=ZoneState.INVALIDATED,
        origin_swept=False,
        origin_failure_swing=True,
    )

    block = promote_mitigation(ob=ob, displacement=displacement_at(30))

    assert block is not None
    assert block.polarity is ZonePolarity.BULLISH


# --------------------------------------------------------------------------
# advance_mitigation_block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ZoneState.INVALIDATED, ZoneState.EXPIRED])
def test_terminal_mitigation_block_cannot_transition(state: ZoneState) -> None:
    with pytest.raises(ValueError, match="terminal mitigation block cannot transition"):
        advance_mitigation_block(
            make_mitigation(state=state),
            candle_at(31, open_="101", high="102", low="100", close="101"),
        )


def test_bullish_mitigation_block_is_invalidated_below_the_band() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(),
        candle_at(31, open_="100", high="100.5", low="98", close="99"),
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_bearish_mitigation_block_is_invalidated_above_the_band() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="102", high="104", low="101.5", close="103"),
    )

    assert advanced.state is ZoneState.INVALIDATED


def test_untouched_mitigation_block_is_unchanged() -> None:
    block = make_mitigation()

    advanced = advance_mitigation_block(
        block,
        candle_at(31, open_="105", high="107", low="104", close="106"),
    )

    assert advanced is block


def test_bullish_mitigation_block_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(),
        candle_at(31, open_="101", high="103", low="100.5", close="102.5"),
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bullish_mitigation_block_touched_above_the_midpoint_is_only_tested() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(),
        candle_at(31, open_="102", high="103", low="101.6", close="102.5"),
    )

    assert advanced.state is ZoneState.TESTED


def test_bearish_mitigation_block_reaching_the_midpoint_is_mitigated() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="100.5", high="101.5", low="99", close="99.5"),
    )

    assert advanced.state is ZoneState.MITIGATED


def test_bearish_mitigation_block_touched_below_the_midpoint_is_only_tested() -> None:
    advanced = advance_mitigation_block(
        make_mitigation(polarity=ZonePolarity.BEARISH),
        candle_at(31, open_="100.2", high="100.4", low="99", close="99.5"),
    )

    assert advanced.state is ZoneState.TESTED


def test_tested_mitigation_block_touched_again_is_unchanged() -> None:
    block = make_mitigation(state=ZoneState.TESTED)

    advanced = advance_mitigation_block(
        block,
        candle_at(31, open_="102", high="103", low="101.6", close="102.5"),
    )

    assert advanced is block


# --------------------------------------------------------------------------
# Cross-type invariant
# --------------------------------------------------------------------------


def test_promoted_zones_never_inherit_the_parent_polarity() -> None:
    """Breaker and mitigation both invert their parent OB by doctrine."""

    parent = make_ob(state=ZoneState.INVALIDATED)

    breaker = promote_breaker(ob=parent)
    mitigation = promote_mitigation(
        ob=replace(parent, origin_swept=False, origin_failure_swing=True),
    )

    assert breaker is not None
    assert mitigation is not None
    assert breaker.polarity is not parent.polarity
    assert mitigation.polarity is not parent.polarity
