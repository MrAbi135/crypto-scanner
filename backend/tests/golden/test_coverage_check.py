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


def test_the_manifest_covers_every_detection_subsection() -> None:
    """§3 through §8 have 45 subsections between them, and a coverage map that
    silently omits one reports a gap smaller than the real one."""
    ids = {section.id for section in load_manifest()}

    assert len(ids) == 45

    for major, count in ((3, 8), (4, 8), (5, 10), (6, 7), (7, 5), (8, 7)):
        present = {i for i in ids if i.startswith(f"{major}.")}

        assert len(present) == count, f"section {major} should have {count} subsections"
