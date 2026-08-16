"""The declared-history mechanism, and the property that makes it safe.

SLS §1.9 requires ≥ 300 closed candles before structure, liquidity or ICT
detection may run. Golden cases cannot carry 300 hand-written candles, so a
dataset may *declare* its history with a `filler` block instead.

The mechanism is only sound if declared history is genuinely inert: it must
satisfy the warm-up count without contributing a detection of its own, and
without shifting the answer the scenario was labelled for. These tests assert
exactly that, because a filler that quietly produced a swing would corrupt
every dataset built on it at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.golden.harness.dataset import load_dataset
from tests.golden.harness.runner import run_dataset

SCENARIO = [
    {
        "open_time": "2026-01-05T00:00:00+00:00",
        "open": "9.5",
        "high": "10",
        "low": "9",
        "close": "9.8",
    },
    {
        "open_time": "2026-01-05T00:05:00+00:00",
        "open": "10.2",
        "high": "11",
        "low": "10",
        "close": "10.8",
    },
    {
        "open_time": "2026-01-05T00:10:00+00:00",
        "open": "14.2",
        "high": "15",
        "low": "14",
        "close": "14.8",
    },
    {
        "open_time": "2026-01-05T00:15:00+00:00",
        "open": "10.8",
        "high": "11",
        "low": "10",
        "close": "10.2",
    },
    {
        "open_time": "2026-01-05T00:20:00+00:00",
        "open": "9.8",
        "high": "10",
        "low": "9",
        "close": "9.2",
    },
    {
        "open_time": "2026-01-05T00:25:00+00:00",
        "open": "8.8",
        "high": "9",
        "low": "8",
        "close": "8.2",
    },
    {
        "open_time": "2026-01-05T00:30:00+00:00",
        "open": "7.8",
        "high": "8",
        "low": "7",
        "close": "7.2",
    },
]

# Flat and well below the scenario, so it can neither form a swing of its own
# nor be the extreme of any window the scenario's swings are judged in.
FILLER = {"count": 20, "open": "5", "high": "5.5", "low": "4.5", "close": "5"}


def write_dataset(tmp_path: Path, **overrides: Any) -> Path:
    payload: dict[str, Any] = {
        "dataset_id": "filler-fixture",
        "engine": "structure",
        "sls_sections": ["1.9", "3.1"],
        "description": "fixture",
        "labelling_rationale": "x" * 200,
        "labelled_by": "test",
        "labelled_at": "2026-08-17",
        "algo_version": "s4-test",
        "symbol": "FILLERTEST",
        "timeframe": "M5",
        "candles": SCENARIO,
        "expected": {},
    }
    payload.update(overrides)

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_dataset_without_filler_is_unchanged(tmp_path: Path) -> None:
    dataset = load_dataset(write_dataset(tmp_path))

    assert dataset.filler_count == 0
    assert dataset.scenario_start_index == 0
    assert len(dataset.candles) == len(SCENARIO)


def test_filler_is_prepended_and_stays_contiguous(tmp_path: Path) -> None:
    dataset = load_dataset(write_dataset(tmp_path, filler=FILLER))

    assert dataset.filler_count == 20
    assert dataset.scenario_start_index == 20
    assert len(dataset.candles) == 20 + len(SCENARIO)

    # The loader's contiguity check already ran; assert the seam explicitly so
    # a future change to the arithmetic cannot pass silently.
    step = dataset.timeframe.duration
    assert dataset.candles[20].open_time == dataset.candles[19].open_time + step
    assert dataset.candles[20].open_time.isoformat() == SCENARIO[0]["open_time"]
    assert dataset.candles[0].open_time == dataset.candles[20].open_time - step * 20


def test_zero_filler_is_the_same_as_none(tmp_path: Path) -> None:
    dataset = load_dataset(write_dataset(tmp_path, filler={**FILLER, "count": 0}))

    assert dataset.filler_count == 0
    assert len(dataset.candles) == len(SCENARIO)


def test_negative_filler_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filler count must be non-negative"):
        load_dataset(write_dataset(tmp_path, filler={**FILLER, "count": -1}))


async def test_declared_history_only_ever_adds_detections(tmp_path: Path) -> None:
    """Filler is additive, not neutral — and the difference matters.

    An earlier version of this test asserted that declared history cannot
    change the verdict at all. That is **false**, and it passed only because
    this fixture's scenario happens to confirm no external swing either way.

    Real history genuinely enables detections that a short series cannot
    reach: an external swing needs `k_ext = 5` candles on its left, so a
    scenario candle near the start of a bare series is unjudgeable and
    becomes judgeable once history exists. That is the engine seeing what it
    would really see, and it is the whole reason for declaring history.

    What must hold is the weaker, true property: every detection the bare run
    produced still appears in the padded run, unchanged apart from its index
    offset. Filler may **add**; it may never remove or alter.
    """

    bare = await run_dataset(load_dataset(write_dataset(tmp_path / "a", **{})))
    padded = await run_dataset(load_dataset(write_dataset(tmp_path / "b", filler=FILLER)))

    def fingerprint(event: dict[str, Any], offset: int) -> tuple[Any, ...]:
        return (
            event["event_type"],
            event["payload"]["index"] - offset,
            event["payload"]["price"],
        )

    bare_events = {fingerprint(event, 0) for event in bare["events"]}
    padded_events = {fingerprint(event, 20) for event in padded["events"]}

    assert bare_events <= padded_events, "declared history removed or altered a detection"
    assert padded["report"]["internal_swings"] >= bare["report"]["internal_swings"]
    assert padded["report"]["external_swings"] >= bare["report"]["external_swings"]


async def test_declared_history_emits_no_detection_of_its_own(tmp_path: Path) -> None:
    """Filler alone must produce nothing at all.

    A flat window confirms no swing under §3.1 — the same rule the
    flat-window dataset pins — so 300 identical candles add history without
    adding a single fact.
    """

    only_filler = write_dataset(
        tmp_path,
        candles=[
            {
                "open_time": "2026-01-05T00:00:00+00:00",
                "open": "5",
                "high": "5.5",
                "low": "4.5",
                "close": "5",
            }
        ],
        filler={**FILLER, "count": 300},
    )

    result = await run_dataset(load_dataset(only_filler))

    assert result["report"]["candles"] == 301
    assert result["report"]["internal_swings"] == 0
    assert result["report"]["external_swings"] == 0
    assert result["events"] == []
