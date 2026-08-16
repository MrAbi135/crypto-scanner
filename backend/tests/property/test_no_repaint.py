"""The no-repaint theorem, as an executable property (Roadmap S4 DoD).

The product's first sentence promises a *"deterministic, non-repainting"*
doctrine, and Constitution §29.3 requires that identical market data always
produces identical analysis. Until now nothing verified either claim.

**Golden datasets structurally cannot.** A golden case replays one fixed series
and compares one output; repainting is about what happens when *more candles
arrive*, and a fixed series has no "later". Twelve datasets or a hundred and
ninety, the question is never asked. It needs a property over generated inputs,
which is exactly what §32.5 and the S4 DoD call for.

The theorem, stated plainly: **a confirmed swing is a historical fact.** Once
the engine has seen `k` candles either side of a pivot and confirmed it, no
amount of subsequent price action may delete that swing, move it, or change its
price. SLS §3.1 puts it as *"the swing never existed for the engine before its
confirmation moment"* — and the mirror obligation is that it never stops
existing afterwards.

Everything downstream inherits this: Constitution §30.3 makes the swing engine
the single shared implementation behind structure, liquidity and ICT. If swings
repaint, every zone, pool and signal built on them repaints too.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.support.strategies import candle_series

from scanner.domain.structure import (
    SwingStrength,
    classify_swings,
    detect_external_swings,
    detect_internal_swings,
    detect_swings,
    swing_window,
)

pytestmark = pytest.mark.property

# 2*k_ext + 1 = 11 is the shortest series that can confirm an external swing,
# so generate past it or the external cases would vacuously pass.
_MIN_SERIES = 12


@given(
    series=candle_series(min_size=_MIN_SERIES, max_size=80),
    data=st.data(),
)
@pytest.mark.parametrize(
    "strength",
    [SwingStrength.INTERNAL, SwingStrength.EXTERNAL],
)
def test_a_confirmed_swing_is_never_revoked_by_later_candles(
    series: list,
    data: st.DataObject,
    strength: SwingStrength,
) -> None:
    """The theorem itself.

    Every swing confirmed on a prefix must still be present, identical, in the
    full series. Set containment rather than equality: later candles may
    confirm *additional* swings, which is growth, not repainting.
    """

    split = data.draw(
        st.integers(min_value=0, max_value=len(series)),
        label="prefix length",
    )

    confirmed_early = set(detect_swings(series[:split], strength=strength))
    confirmed_later = set(detect_swings(series, strength=strength))

    missing = confirmed_early - confirmed_later

    assert not missing, (
        f"{len(missing)} swing(s) confirmed on the first {split} candles "
        f"vanished or changed once the series grew to {len(series)}"
    )


@given(series=candle_series(min_size=_MIN_SERIES, max_size=60), data=st.data())
def test_a_swing_depends_only_on_its_own_confirmation_window(
    series: list,
    data: st.DataObject,
) -> None:
    """Why the theorem holds, asserted independently of the theorem.

    §3.1 judges a pivot against `k` candles either side and nothing else. So
    truncating the series anywhere beyond a swing's window must leave that
    swing untouched — if some far-away candle could reach back and alter a
    verdict, non-repainting would be luck rather than structure.
    """

    strength = SwingStrength.INTERNAL
    window = swing_window(strength)
    full = detect_swings(series, strength=strength)

    if not full:
        return

    swing = data.draw(st.sampled_from(full), label="swing under test")

    # Everything the swing is entitled to see, and not one candle more.
    truncated = series[: swing.index + window + 1]

    assert swing in set(detect_swings(truncated, strength=strength))


@given(series=candle_series(min_size=_MIN_SERIES, max_size=60))
def test_detection_is_deterministic_across_runs(series: list) -> None:
    """Constitution §29.3: identical data, identical analysis, byte for byte.

    The golden suite checks this three times over twelve fixed datasets; this
    checks it over generated ones, where the shapes are not of my choosing.
    """

    assert detect_internal_swings(series) == detect_internal_swings(series)
    assert detect_external_swings(series) == detect_external_swings(series)


@given(series=candle_series(min_size=_MIN_SERIES, max_size=80), data=st.data())
def test_an_assigned_label_is_immutable(series: list, data: st.DataObject) -> None:
    """SLS §3.3: *"Labels are immutable once assigned (facts)."*

    A swing labelled HH cannot later become LH because the market moved on.
    §3.3 classifies against the immediate same-kind *predecessor*, which is
    already in the past — so appending candles must never rewrite a label.
    """

    split = data.draw(st.integers(min_value=0, max_value=len(series)), label="prefix")

    early = {
        item.swing: item.label for item in classify_swings(detect_internal_swings(series[:split]))
    }
    later = {item.swing: item.label for item in classify_swings(detect_internal_swings(series))}

    rewritten = {
        swing: (label, later[swing])
        for swing, label in early.items()
        if swing in later and later[swing] is not label
    }

    assert not rewritten, f"labels changed after more candles arrived: {rewritten}"


@given(series=candle_series(min_size=_MIN_SERIES, max_size=60))
def test_every_external_swing_is_also_an_internal_swing(series: list) -> None:
    """SLS §3.1: *"Every external swing is by construction also an internal one."*

    A structural invariant rather than a repaint one, but it belongs here: it
    is only true if both strengths judge the same pivots by the same rule, and
    a divergence would mean the shared swing engine had quietly forked.
    """

    internal_pivots = {(s.index, s.kind, s.price) for s in detect_internal_swings(series)}

    for external in detect_external_swings(series):
        assert (external.index, external.kind, external.price) in internal_pivots
