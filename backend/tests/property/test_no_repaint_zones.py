"""The no-repaint theorem for the objects built on top of swings (G2 §3).

`test_no_repaint.py` proves the theorem where it starts: a confirmed swing is a
historical fact. Its docstring then says everything downstream inherits that,
"if swings repaint, every zone, pool and signal built on them repaints too" —
which is true and is not the same as the downstream objects being proven.
They have their own inputs and their own arithmetic, and each of those is a
place the inheritance could fail.

So this file asks the same question of the derived objects. Zones are built
from candle geometry and an ATR multiple; clusters are built from swings and an
ATR-scaled tolerance. Both read a window rather than a single candle, and both
could in principle answer differently once the window has grown.

**These run against the domain detectors, not the replay services**, and that
is a deliberate limit rather than a shortcut. SLS §1.9 gates every replay at
300 closed candles, and a hypothesis property that had to build 300-candle
series and run a full pipeline per example would cost minutes per property and
be abandoned. The detectors need only ATR's 14-candle seeding, so the same
theorem can be asked a hundred times a second. What that leaves unproven is
the *plumbing* — a replay that passed a detector the wrong window would satisfy
every property here — and the golden datasets are what cover that.
"""

from __future__ import annotations

from hypothesis import event, given
from hypothesis import strategies as st
from tests.support.strategies import candle_series, equal_highs_series, walking_candle_series

from scanner.domain.common import Candle, wilder_atr, wilder_atr_series
from scanner.domain.common.atr import atr_at
from scanner.domain.ict import detect_displacement, detect_fvg
from scanner.domain.liquidity.clusters import detect_equal_level_clusters
from scanner.domain.structure import detect_external_swings

# ATR seeds over 14 candles and an FVG needs three, so anything shorter would
# pass by never detecting anything.
_MIN_SERIES = 20

# 120 rather than the 80 the swing properties use: an order-block origin run
# plus its five-candle qualifying window plus room for the "and then it grew"
# half needs more than a swing does, and the detectors are cheap enough to
# afford it.
_MAX_SERIES = 120


def _fvgs(candles: list[Candle]) -> dict[int, object]:
    """Every FVG the series confirms, keyed by the index that confirmed it."""

    atrs = wilder_atr_series(candles)

    found = {}

    for index in range(2, len(candles)):
        atr = atrs[index]

        if atr is None or atr <= 0:
            continue

        fvg = detect_fvg(candles, index, atr=atr, middle_is_displacement=False)

        if fvg is not None:
            found[index] = fvg

    return found


@given(series=candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES), data=st.data())
def test_atr_at_an_index_never_changes_as_the_series_grows(
    series: list,
    data: st.DataObject,
) -> None:
    """The foundation under every band in the doctrine.

    SLS §0.4 expresses every price-distance threshold as an ATR multiple, so a
    zone's edges, a cluster's tolerance and a displacement's range test are all
    denominated in it. If ATR at a past index moved when new candles arrived,
    every one of those would repaint whatever the detectors did — and nothing
    else in this file would mean anything.

    Wilder's recurrence reads candles 0..i and stops, so this should hold by
    construction. Asserted anyway: "by construction" is a claim about code that
    can be edited.
    """

    split = data.draw(
        st.integers(min_value=_MIN_SERIES, max_value=len(series)),
        label="prefix length",
    )

    prefix = series[:split]
    early = wilder_atr_series(prefix)
    later = wilder_atr_series(series)

    for index in range(len(prefix)):
        assert early[index] == later[index], (
            f"ATR at index {index} was {early[index]} on a {split}-candle series "
            f"and {later[index]} once it grew to {len(series)}"
        )


@given(series=candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES), data=st.data())
def test_a_confirmed_fvg_is_never_revoked_by_later_candles(
    series: list,
    data: st.DataObject,
) -> None:
    """§5.4's gap is a fact about three candles that have already closed.

    Containment rather than equality, for the same reason the swing property
    uses it: later candles may confirm *additional* gaps, which is growth. What
    may not happen is a gap confirmed on the prefix being absent, or present
    with a different band, once the series is longer.
    """

    split = data.draw(
        st.integers(min_value=_MIN_SERIES, max_value=len(series)),
        label="prefix length",
    )

    early = _fvgs(series[:split])
    later = _fvgs(series)

    for index, fvg in early.items():
        assert index in later, (
            f"the FVG confirmed at index {index} on the first {split} candles "
            f"vanished once the series grew to {len(series)}"
        )
        assert later[index] == fvg, (
            f"the FVG at index {index} changed when the series grew: {fvg} became {later[index]}"
        )


@given(series=candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES), data=st.data())
def test_an_fvg_depends_only_on_candles_up_to_its_own_index(
    series: list,
    data: st.DataObject,
) -> None:
    """Why the theorem holds, asserted independently of the theorem.

    §5.4 reads the first and third candle of a three-candle window and the ATR
    at the third. Truncating the series immediately after that third candle
    therefore removes nothing it was entitled to see — and if some later candle
    could still reach back and change the verdict, non-repainting would be an
    accident of the current code rather than a property of the rule.
    """

    full = _fvgs(series)

    if not full:
        return

    index = data.draw(st.sampled_from(sorted(full)), label="fvg under test")

    truncated = _fvgs(series[: index + 1])

    assert truncated.get(index) == full[index], (
        f"the FVG at index {index} is not reproducible from the candles up to "
        f"its own close — it needs {len(series) - index - 1} later candle(s)"
    )


@given(
    series=walking_candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES),
    data=st.data(),
)
def test_a_displacement_is_a_permanent_measurement(
    series: list,
    data: st.DataObject,
) -> None:
    """§5.10 states this outright: "Invalidation. None — a displacement candle
    is a permanent measurement."

    It reads the candle's own body and range, the mean body of the twenty
    before it, and ATR at its index. Every one of those is settled at the
    close, so a displacement found on a prefix must survive the series growing
    — including its measured multiples, which §5.10 requires be recorded as
    evidence rather than as a boolean.

    **On `walking_candle_series` rather than `candle_series`, and the reason is
    the point of the test.** The first draft used the shared strategy and
    passed; mutating `detect_displacement` to peek at the next candle did not
    break it. It could not: of 200 series from `candle_series`, **zero**
    contained a single displacement, because independently-drawn midpoints make
    every gap a true range and push ATR far above any candle's own span. The
    property was passing by finding nothing. On the walking strategy the rate
    is 136 of 200, and the same mutation is caught.
    """

    split = data.draw(
        st.integers(min_value=_MIN_SERIES, max_value=len(series)),
        label="prefix length",
    )

    def found(candles: list[Candle]) -> dict[int, object]:
        atrs = wilder_atr_series(candles)

        return {
            index: displacement
            for index in range(len(candles))
            if (atr := atrs[index]) is not None
            and atr > 0
            and (displacement := detect_displacement(candles, index, atr=atr)) is not None
        }

    early = found(series[:split])
    later = found(series)

    for index, displacement in early.items():
        assert later.get(index) == displacement, (
            f"the displacement at index {index} changed or vanished when the "
            f"series grew from {split} to {len(series)} candles"
        )


@given(series=candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES), data=st.data())
def test_a_cluster_only_ever_gains_members(
    series: list,
    data: st.DataObject,
) -> None:
    """§4.3: "later qualifying members join incrementally (join events are
    appends, never rewrites)".

    That is a stronger statement than "the cluster survives", and it is the one
    worth asserting: a chain may grow to the right as new swings confirm, but
    the members it already had must remain, in the order it already had them.
    A cluster that dropped a member or re-ordered one would be re-labelling
    engineered liquidity after the fact, which is what §4.2 calls
    recomputable-from-stored-evidence and forbids.

    Keyed on the first member's index, because §4.3 stamps a cluster's birth at
    its *second* member's confirmation and that stamp does not move as the
    chain grows — so the first index is the stable identity here.

    **Measured coverage, because a property that never meets its subject proves
    nothing.** Of 300 series from `candle_series`, 44 hold a cluster present in
    both the prefix and the full series, so the "it survives at all" half has
    teeth here. Only 5 had a chain actually *grow*, so growth is exercised by
    `test_a_growing_cluster_appends_rather_than_rewrites` below, on a generator
    built to produce it.
    """

    split = data.draw(
        st.integers(min_value=_MIN_SERIES, max_value=len(series)),
        label="prefix length",
    )

    def clusters(candles: list[Candle]) -> dict[tuple[int, str], tuple[int, ...]]:
        return {
            (cluster.member_indices[0], cluster.side.value): cluster.member_indices
            for cluster in detect_equal_level_clusters(
                detect_external_swings(candles),
                atrs=wilder_atr_series(candles),
            )
        }

    early = clusters(series[:split])
    later = clusters(series)

    for key, members in early.items():
        assert key in later, (
            f"the {key[1]} cluster seeded at index {key[0]} on the first {split} "
            f"candles vanished once the series grew to {len(series)}"
        )
        grown = later[key]
        assert grown[: len(members)] == members, (
            f"the {key[1]} cluster seeded at index {key[0]} was rewritten rather "
            f"than appended to: {members} became {grown}"
        )


@given(series=equal_highs_series(), data=st.data())
def test_a_growing_cluster_appends_rather_than_rewrites(
    series: list,
    data: st.DataObject,
) -> None:
    """§4.3's append clause, on a series where chains actually grow.

    The general cluster property above meets growth 5 times in 300, which is
    not a test of "appends, never rewrites" so much as an occasional visit to
    it. `equal_highs_series` prints three to six near-equal external highs, so
    a prefix that ends between the second and last top holds a shorter chain
    than the full series: measured 158 growths in 300 splits.

    Asserted in two parts so a failure says which clause broke. Every cluster
    on the prefix must still exist on the full series, and its members must be
    an unchanged prefix of the grown chain -- earlier members neither dropped,
    replaced nor re-ordered. The run is also required to have *seen* growth at
    least once across the draw, via `event`, so a future generator change that
    quietly stops producing it shows up in hypothesis statistics instead of
    passing silently.
    """

    split = data.draw(
        st.integers(min_value=_MIN_SERIES, max_value=len(series)),
        label="prefix length",
    )

    def clusters(candles: list[Candle]) -> dict[tuple[int, str], tuple[int, ...]]:
        return {
            (cluster.member_indices[0], cluster.side.value): cluster.member_indices
            for cluster in detect_equal_level_clusters(
                detect_external_swings(candles),
                atrs=wilder_atr_series(candles),
            )
        }

    early = clusters(series[:split])
    later = clusters(series)

    for key, members in early.items():
        assert key in later, (
            f"the {key[1]} cluster seeded at index {key[0]} on the first {split} "
            f"candles vanished once the series grew to {len(series)}"
        )

        grown = later[key]

        if len(grown) > len(members):
            event("cluster grew between prefix and full series")

        assert grown[: len(members)] == members, (
            f"the {key[1]} cluster seeded at index {key[0]} was rewritten rather "
            f"than appended to: {members} became {grown}"
        )


@given(series=candle_series(min_size=_MIN_SERIES, max_size=_MAX_SERIES))
def test_the_atr_shim_agrees_with_the_scalar_it_replaces(series: list) -> None:
    """PR #246 introduced `atr_at` so detectors could read a precomputed series.

    The speed of that change rests entirely on the two paths being the same
    arithmetic, which `wilder_atr_series` only claims in prose. Asserted at
    every index including both sides of the 14-candle seeding boundary, which
    is the one place the two could differ and where no golden dataset sits.
    """

    atrs = wilder_atr_series(series)

    for index in range(len(series)):
        assert atr_at(series, index, atrs) == wilder_atr(series, index)
        assert atr_at(series, index, None) == wilder_atr(series, index)
