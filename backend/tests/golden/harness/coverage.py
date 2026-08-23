"""The SLS clause -> dataset map, and what makes it honest.

Roadmap v2.0.0 §8.1 replaced the per-sprint case counts with a coverage bar:

    Every rule and every named edge case in the governing SLS section has at
    least one golden case asserting it, and the mapping from SLS clause ->
    dataset is machine-checked in CI. A clause with no case fails the build.

It also says why: *"a count is a proxy that can be satisfied without satisfying
the thing it proxies for"*. This module is the machine-checked half.

## Why a rule can be `pending`

Requiring every one of §3-§8's rules to be covered today would fail the build
on the first run and stay failing, which teaches a team to ignore the check.
So each rule carries a status, and the manifest is the honest record rather
than the aspiration:

* `covered` -- a dataset declares this rule id, and the check proves it does;
* `pending` -- nobody covers it, and `blocked_on` says what stands in the way.

## The property that keeps the map from rotting

A `pending` rule that a dataset *does* cover is an error. That is the rule this
module exists for: the previous coverage record was a hand-written table in the
README, and it went stale -- it still described EQH/EQL clustering as having "no
caller" months after it was wired. A map nobody is forced to update describes
the repository it was written against, not the one in front of you.

So coverage can only move by editing the manifest, in the same commit as the
dataset. Both directions fail loudly.

## Enumeration is itself incomplete, and says so

Eight of the forty-five detection subsections have had their rules written out.
The rest carry a single stub entry. `enumerated` marks the difference, because
"0 of 1 rules covered" for a stubbed section is not the same claim as "0 of 11"
for an enumerated one, and reporting them alike would understate the gap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parent.parent / "coverage.json"

COVERED = "covered"
PENDING = "pending"


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    rule: str
    status: str
    blocked_on: str | None


@dataclass(frozen=True, slots=True)
class Section:
    id: str
    title: str
    enumerated: bool
    rules: tuple[Rule, ...]


@dataclass(frozen=True, slots=True)
class CoverageReport:
    sections: tuple[Section, ...]
    problems: tuple[str, ...]

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(rule for section in self.sections for rule in section.rules)

    @property
    def covered(self) -> int:
        return sum(1 for rule in self.rules if rule.status == COVERED)

    @property
    def enumerated_sections(self) -> int:
        return sum(1 for section in self.sections if section.enumerated)

    def summary(self) -> str:
        return (
            f"{self.covered}/{len(self.rules)} SLS rules covered across "
            f"{self.enumerated_sections}/{len(self.sections)} enumerated sections"
        )


def load_manifest() -> tuple[Section, ...]:
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))

    return tuple(
        Section(
            id=section["id"],
            title=section["title"],
            enumerated=section["enumerated"],
            rules=tuple(
                Rule(
                    id=rule["id"],
                    rule=rule["rule"],
                    status=rule["status"],
                    blocked_on=rule.get("blocked_on"),
                )
                for rule in section["rules"]
            ),
        )
        for section in raw["sections"]
    )


def check_coverage(claims: dict[str, tuple[str, ...]]) -> CoverageReport:
    """Reconcile the manifest against what the datasets claim.

    `claims` maps dataset id to the rule ids it declares. Four ways to fail,
    and each names the edit that fixes it.
    """
    sections = load_manifest()

    known = {rule.id: rule for section in sections for rule in section.rules}

    asserted: dict[str, list[str]] = {}

    for dataset_id, rule_ids in claims.items():
        for rule_id in rule_ids:
            asserted.setdefault(rule_id, []).append(dataset_id)

    problems: list[str] = []

    for rule_id, dataset_ids in sorted(asserted.items()):
        rule = known.get(rule_id)

        if rule is None:
            problems.append(
                f"{', '.join(dataset_ids)} declares unknown rule '{rule_id}' -- "
                f"add it to coverage.json or fix the typo"
            )
            continue

        if rule.status == PENDING:
            problems.append(
                f"rule '{rule_id}' is marked pending but {', '.join(dataset_ids)} "
                f"covers it -- mark it covered in coverage.json"
            )

    for rule in known.values():
        if rule.status == COVERED and rule.id not in asserted:
            problems.append(
                f"rule '{rule.id}' is marked covered and no dataset declares it -- "
                f"either a dataset lost its sls_rules entry or the manifest is wrong"
            )

        if rule.status not in {COVERED, PENDING}:
            problems.append(f"rule '{rule.id}' has unknown status '{rule.status}'")

        if rule.status == PENDING and not rule.blocked_on:
            problems.append(
                f"rule '{rule.id}' is pending without a `blocked_on` -- "
                f"an unexplained gap is the kind that never closes"
            )

    return CoverageReport(sections=sections, problems=tuple(problems))
