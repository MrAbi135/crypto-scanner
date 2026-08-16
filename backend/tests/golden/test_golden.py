"""The golden gate: curated datasets must pass, and runs must be identical.

Constitution §32.3: *a detector without a golden dataset may not ship.*
§32.4: golden datasets grow monotonically; a passing case may never be
deleted or weakened to make a change ship. §32.5 / SLS §0: identical input
plus identical parameters must yield byte-identical output.

This module is the executable form of all three.
"""

from __future__ import annotations

import pytest

from tests.golden.harness.canonical import (
    canonical_bytes,
    canonical_text,
    canonicalise,
    output_hash,
)
from tests.golden.harness.dataset import GoldenDataset, discover_datasets
from tests.golden.harness.runner import run_dataset

DATASETS = discover_datasets()

# Guard against the suite silently emptying itself — a passing run over zero
# datasets is the failure mode this whole gate exists to prevent.
MINIMUM_DATASETS = 3


def dataset_id(dataset: GoldenDataset) -> str:
    return dataset.dataset_id


def test_datasets_are_discovered() -> None:
    assert len(DATASETS) >= MINIMUM_DATASETS, (
        f"expected at least {MINIMUM_DATASETS} golden datasets, found {len(DATASETS)}. "
        "Datasets grow monotonically (Constitution §32.4) — they are never removed."
    )


@pytest.mark.parametrize("dataset", DATASETS, ids=dataset_id)
async def test_dataset_matches_its_labelled_expectation(dataset: GoldenDataset) -> None:
    actual = await run_dataset(dataset)

    if canonical_bytes(actual) != canonical_bytes(dataset.expected):
        pytest.fail(
            f"{dataset.dataset_id} ({dataset.path.name}) diverged from doctrine.\n"
            f"SLS sections: {', '.join(dataset.sls_sections)}\n\n"
            f"--- expected ---\n{canonical_text(dataset.expected)}\n\n"
            f"--- actual ---\n{canonical_text(actual)}\n\n"
            "If the engine is right and the label is wrong, fix the label AND its "
            "labelling_rationale. If the label is right, the engine has a defect."
        )


@pytest.mark.parametrize("dataset", DATASETS, ids=dataset_id)
async def test_replay_is_byte_identical_across_runs(dataset: GoldenDataset) -> None:
    """Roadmap S3 DoD: same input, byte-identical output, run three times."""

    hashes = {output_hash(await run_dataset(dataset)) for _ in range(3)}

    assert len(hashes) == 1, (
        f"{dataset.dataset_id} is not deterministic across runs: {sorted(hashes)}"
    )


@pytest.mark.parametrize("dataset", DATASETS, ids=dataset_id)
def test_dataset_declares_usable_provenance(dataset: GoldenDataset) -> None:
    """Constitution §32.3-§32.4: a case a reviewer cannot trace is not a label."""

    assert dataset.sls_sections, "must cite the doctrine it encodes"
    assert dataset.labelled_by.strip()
    assert dataset.labelled_at.strip()
    assert dataset.algo_version.strip()
    assert len(dataset.labelling_rationale) >= 120, (
        "labelling_rationale must actually derive the expectation from the SLS, "
        "not restate the description"
    )


# --------------------------------------------------------------------------
# Harness self-test (Roadmap S3 DoD)
#
# The harness is the instrument. An instrument that cannot fail cannot
# measure, so these assert that it detects the differences it claims to.
# --------------------------------------------------------------------------


def test_canonical_form_is_insensitive_to_key_order() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})


def test_canonical_form_distinguishes_real_differences() -> None:
    assert canonical_bytes({"price": "15"}) != canonical_bytes({"price": "15.0"})
    assert output_hash([1, 2]) != output_hash([2, 1])


def test_canonical_form_rejects_floats() -> None:
    """A float reaching the comparison boundary is a no-float-law breach."""

    with pytest.raises(TypeError, match="no-float law"):
        canonical_bytes({"price": 15.0})


def test_canonical_form_rejects_naive_datetimes() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="naive datetime"):
        canonicalise(datetime(2026, 1, 1))


def test_canonical_form_normalises_equivalent_instants() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    utc = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    plus_five = datetime(2026, 1, 5, 17, 0, tzinfo=timezone(timedelta(hours=5)))

    assert canonicalise(utc) == canonicalise(plus_five)


def test_decimal_precision_survives_canonicalisation() -> None:
    from decimal import Decimal

    exact = Decimal("101.123456789012345678")

    assert canonicalise(exact) == "101.123456789012345678"


async def test_harness_detects_a_wrong_expectation() -> None:
    """The gate must fail when the label and the engine disagree.

    Without this, a broken comparison would let every dataset 'pass' and the
    whole suite would be decorative.
    """

    dataset = DATASETS[0]
    actual = await run_dataset(dataset)

    corrupted = {**actual, "report": {**actual["report"], "internal_swings": 999}}

    assert canonical_bytes(actual) != canonical_bytes(corrupted)
