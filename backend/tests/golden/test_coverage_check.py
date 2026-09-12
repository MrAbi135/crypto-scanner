"""The coverage check's own failure modes.

`test_golden.py` asserts the repository is currently consistent, which is a
statement about today's manifest. It cannot show that the check *would* have
caught an inconsistency -- and a check that cannot fail is the defect this
whole mechanism exists to prevent, so it gets its own tests.
"""

from __future__ import annotations

from tests.golden.harness.coverage import check_coverage, load_manifest


def _one(status: str) -> str:
    """A rule id from the manifest with the given status."""
    return next(
        rule.id for section in load_manifest() for rule in section.rules if rule.status == status
    )


def _real_claims() -> dict[str, tuple[str, ...]]:
    from tests.golden.harness.dataset import discover_datasets

    return {dataset.dataset_id: dataset.sls_rules for dataset in discover_datasets()}


def test_the_repository_as_it_stands_is_consistent() -> None:
    assert not check_coverage(_real_claims()).problems


def test_a_covered_rule_nobody_asserts_is_caught() -> None:
    """The manifest claiming coverage that no dataset provides."""
    claims = _real_claims()

    covered = _one("covered")

    stripped = {
        dataset_id: tuple(r for r in rules if r != covered) for dataset_id, rules in claims.items()
    }

    problems = check_coverage(stripped).problems

    assert any(covered in problem and "no dataset declares it" in problem for problem in problems)


def test_a_pending_rule_that_gained_a_dataset_is_caught() -> None:
    """The direction that keeps the map from rotting.

    Coverage arriving without the manifest being edited is how the README's
    table came to describe a wired detector as unwired.
    """
    pending = _one("pending")

    claims = {**_real_claims(), "some-new-case": (pending,)}

    problems = check_coverage(claims).problems

    assert any(pending in problem and "marked pending" in problem for problem in problems)


def test_a_typo_in_a_rule_id_is_caught() -> None:
    """A dataset citing a rule that does not exist covers nothing at all."""
    claims = {**_real_claims(), "typo-case": ("3.1-swing-hihg",)}

    problems = check_coverage(claims).problems

    assert any("unknown rule" in problem for problem in problems)


def test_every_pending_rule_explains_itself() -> None:
    """`blocked_on` is required, because an unexplained gap is the kind that
    never closes -- nobody can tell whether it is a morning's work or a sprint."""
    for section in load_manifest():
        for rule in section.rules:
            if rule.status == "pending":
                assert rule.blocked_on, f"{rule.id} is pending with no reason given"


def test_every_subsection_is_enumerated() -> None:
    """Roadmap §8.1 as amended in v2.1.0: enumeration is part of "map complete",
    and *"a subsection still carrying a stub fails the build"*.

    A stub hides a section's real size rather than reporting it. §5.1 read
    `0/1` while carrying seventeen rules, and §8.3, §8.5 and §8.6 each read
    "not enumerated" while already holding a covered rule. A new SLS
    subsection arriving as a stub must fail here rather than quietly shrinking
    the denominator that G2 is measured against.
    """
    stubs = sorted(section.id for section in load_manifest() if not section.enumerated)

    assert not stubs, (
        f"these subsections still carry a stub entry: {stubs}. Write their rules out in "
        "coverage.json; Roadmap §8.1 counts enumeration as part of 'map complete'."
    )


def test_the_manifest_covers_every_detection_subsection() -> None:
    """§3 through §8 have 45 subsections between them, and a coverage map that
    silently omits one reports a gap smaller than the real one."""
    ids = {section.id for section in load_manifest()}

    assert len(ids) == 45

    for major, count in ((3, 8), (4, 8), (5, 10), (6, 7), (7, 5), (8, 7)):
        present = {i for i in ids if i.startswith(f"{major}.")}

        assert len(present) == count, f"section {major} should have {count} subsections"


# The datasets whose labels the developer has not personally verified yet.
#
# **Gate G2 requires this set to be EMPTY** (Roadmap §8.2; Constitution §5 makes
# the verification non-delegable). Until it is, the debt is named here rather
# than counted by hand -- because counting it by hand is how it drifted: the
# tally was taken with `grep "PENDING DEVELOPER VERIFICATION"`, which is
# case-sensitive, and three datasets spell it in lower case. Two pending
# datasets were reported to the developer on 2026-09-07; there were five.
#
# Asserted as equality, not containment, so the set can only ever shrink on
# purpose: verifying a dataset without deleting its line here fails just as
# loudly as adding a new unverified one.
UNVERIFIED_LABELS = frozenset(
    {
        "a-spike-under-the-quote-floor-does-not-confirm.json",
        "volume-spike-on-a-doji-is-absorption.json",
        "a-quiet-market-fails-structure-and-zone.json",
        "expansion-needs-all-three-tests.json",
        "a-volume-only-lull-is-not-contraction.json",
        "both-dimensions-contract.json",
        "abnormal-volume-cross-validation.json",
        "range-expansion-is-measured-against-a-moving-atr.json",
        "a-walking-coil-is-not-a-coil.json",
        "one-candle-that-saturates-three-components.json",
        "a-spike-takes-its-direction-from-the-body.json",
        "choch-then-mss-on-a-failure-swing.json",
        "a-choch-without-displacement-is-not-an-mss.json",
        "a-choch-with-no-follow-through-returns-the-trend.json",
        "an-mss-reclaimed-inside-ten-candles-is-low-quality.json",
        "a-choch-with-no-origin-evidence-is-not-an-mss.json",
        "an-order-block-is-the-full-range-of-its-origin-run.json",
        "an-origin-run-stops-at-three-candles.json",
        "a-v-continuation-has-no-order-block.json",
        "a-displacement-with-no-consequence-has-no-order-block.json",
        "a-leg-under-two-atr-registers-no-ote.json",
        "the-ote-band-is-the-62-to-79-retracement.json",
        "a-swept-order-block-that-fails-becomes-a-breaker.json",
        "an-unswept-order-block-that-fails-becomes-a-mitigation-block.json",
    }
)


def _pending_label_files() -> frozenset[str]:
    import json
    from pathlib import Path

    root = Path(__file__).parent / "datasets"

    return frozenset(
        path.name
        for path in root.rglob("*.json")
        if "pending" in json.loads(path.read_text(encoding="utf-8")).get("labelled_by", "").lower()
    )


def test_only_the_named_datasets_await_developer_verification() -> None:
    """G2's "zero datasets left pending developer verification", made countable.

    Nothing in CI asserted this before -- the criterion was checked by eye
    against a case-sensitive grep, and undercounted by three.
    """
    assert _pending_label_files() == UNVERIFIED_LABELS
