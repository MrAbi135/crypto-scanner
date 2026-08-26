"""The pipeline's readers must look where its writers write.

Every service here is constructed with repositories and state managers by
hand, and a constructor argument that is simply *not passed* is not an error —
`StructureReplayService` takes `shift_state` as a keyword with a `None`
default, because the golden harness and the unit tests build it without one.

So omitting it in production wiring is silent, and it stays silent all the way
down: `_seed_trend` returns RANGING on absence, which is a legitimate answer
for a series that has never been graded, and §3.4's diagram starts there.

It was omitted. For as long as it was, `_replay_bos` began every pass in
RANGING, `apply_structure` entered whatever the oldest candles in the window
implied, and — since that method only enters a trend and never leaves one —
the BOS gate held that direction for all five hundred candles. On the host
that meant BTCUSDT H1 replaying BEARISH while the shift engine recorded
BULLISH and price rose from 63,000 to 81,272: no BOS_UP for five days, and
§8.6's A3 unreachable because it wants a break inside the impulse leg.

Nothing failed. The pass logged `trend: BULLISH` — the shift engine's answer,
which was right and was not the one the gate used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scanner.runtime.wiring.detection import build_detection_pipeline


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 26, tzinfo=UTC)


class FakeRedis:
    """Constructors only store it; nothing here calls it."""


def build() -> Any:
    return build_detection_pipeline(
        sessions=None,  # type: ignore[arg-type]
        redis_client=FakeRedis(),  # type: ignore[arg-type]
        clock=FakeClock(),
    )


def test_structure_reads_the_trend_from_where_the_shift_engine_writes_it() -> None:
    """The invariant, stated as the two keys agreeing.

    Asserting `shift_state is not None` would pass on a manager pointed at the
    wrong namespace or stamped with the wrong algo version — both of which
    return `None` from `load` exactly as an unwired one does, and both of which
    reproduce the defect in full.
    """
    pipeline = build()

    structure = pipeline._structure
    shift = pipeline._structure_shift

    assert structure._shift_state is not None
    assert structure._shift_algo_version is not None

    reader = structure._shift_state.context_key("BTCUSDT", "H1", structure._shift_algo_version)
    writer = shift._state.context_key("BTCUSDT", "H1", shift._algo_version)

    assert reader == writer


def test_structure_does_not_seed_from_its_own_namespace() -> None:
    """Its own state carries the trend it *reported* last pass, which is
    `_idle_adjusted`'s output rather than §3.4's maintained state, and reading
    it back would make the gate a function of its own previous answer."""

    pipeline = build()
    structure = pipeline._structure

    own = structure._states.context_key("BTCUSDT", "H1", "s4-v8")
    seed = structure._shift_state.context_key("BTCUSDT", "H1", structure._shift_algo_version)

    assert own != seed
