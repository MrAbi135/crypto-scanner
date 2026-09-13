"""Mutation battery: break the code on purpose, confirm the named tests notice.

Two hand-rolled batteries this month reported wrong verdicts, both from the
same root cause: they scraped pytest's terminal output, and the repository's
`addopts` already carries `-q`, so an extra `-q` became `-qq` and the summary
line they searched for was never printed. The verdicts were recovered only by
reading the raw failure lists by hand.

This runner does not read the terminal. A verdict is a function of pytest's
exit code and its JUnit report, and nothing else:

* **CAUGHT** -- exit 1, and at least one test the spec names *failed* (an
  assertion, not a setup error).
* **MISDIRECTED** -- exit 1, tests failed, but none of the named ones. The
  mutation was noticed, and the spec's claim about *which* test notices it
  is wrong.
* **SURVIVED** -- exit 0. Nothing noticed.
* **ERROR** -- anything else: a patch that does not match exactly once, a
  collection or usage error, a run where only setup errors occurred, or no
  report. An import-time crash is not a test catching a bug.

Guards around that:

* an **unmutated control run** must pass first, with at least one test
  executed -- otherwise every mutation would read as CAUGHT;
* every target file must be **clean in git** before the battery starts, so
  restoring it can never discard someone's uncommitted work;
* each file is restored from its **original bytes** and checked against them
  before the next mutation;
* the pytest `addopts` are replaced for the run, so the repository's
  verbosity and coverage flags cannot change what the runner sees;
* every run gets its own empty bytecode cache, so a same-length mutation
  cannot be imported from `.pyc` compiled from the original source.

Spec (JSON, paths relative to the repository root)::

    {
      "pytest": ["tests/integration/test_publish_path_pg.py", "-m", "integration"],
      "mutations": [
        {"name": "...", "file": "backend/src/...", "old": "...", "new": "...",
         "expect_failing": ["test_a_second_pass"]}
      ]
    }

Run from `backend/`:  uv run python -m tests.support.mutation_battery SPEC.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from tests.support.junit import Outcome, Status, read_report

# Replaces the repository's addopts: strict markers kept, `-q` and coverage
# dropped, because neither may influence a run a program has to interpret.
_ADDOPTS = ["-o", "addopts=--strict-markers", "-p", "no:cacheprovider"]


class Verdict(str, Enum):
    CAUGHT = "CAUGHT"
    MISDIRECTED = "MISDIRECTED"
    SURVIVED = "SURVIVED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Mutation:
    name: str
    file: str
    old: str
    new: str
    expect_failing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int
    outcomes: tuple[Outcome, ...] | None  # None: no report was written


@dataclass(frozen=True, slots=True)
class MutationResult:
    mutation: Mutation
    verdict: Verdict
    detail: str
    failed: tuple[str, ...] = field(default_factory=tuple)


def classify(result: RunResult, expect_failing: Sequence[str]) -> tuple[Verdict, str]:
    """The whole verdict. Pure, so it is unit-tested case by case."""

    if result.outcomes is None:
        return Verdict.ERROR, f"no JUnit report (pytest exit {result.exit_code})"

    failed = [o.test_id for o in result.outcomes if o.status is Status.FAILED]
    errors = [o.test_id for o in result.outcomes if o.status is Status.ERROR]

    if result.exit_code == 0:
        return Verdict.SURVIVED, "every test passed"

    if result.exit_code != 1:
        return Verdict.ERROR, f"pytest exit {result.exit_code} (interrupted, usage or collection)"

    if not failed:
        return Verdict.ERROR, f"exit 1 with no assertion failures; errors: {errors}"

    hits = [test for test in failed if any(expect in test for expect in expect_failing)]

    if hits:
        return Verdict.CAUGHT, f"failed: {hits}"

    return Verdict.MISDIRECTED, f"failed, but not the named tests: {failed}"


def load_spec(path: Path) -> tuple[list[str], list[Mutation]]:
    raw = json.loads(path.read_text(encoding="utf-8"))

    mutations = [
        Mutation(
            name=item["name"],
            file=item["file"],
            old=item["old"],
            new=item["new"],
            expect_failing=tuple(item["expect_failing"]),
        )
        for item in raw["mutations"]
    ]

    for mutation in mutations:
        if not mutation.expect_failing:
            raise ValueError(
                f"{mutation.name}: expect_failing is empty -- CAUGHT would mean nothing"
            )

    return list(raw["pytest"]), mutations


Runner = Callable[[Sequence[str]], RunResult]


def run_pytest(args: Sequence[str], *, cwd: Path) -> RunResult:
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.xml"

        # A fresh, empty bytecode cache for every run. A `.pyc` is trusted when
        # the source's whole-second mtime and size match, so a same-length
        # mutation (`a + b` -> `a - b`, `70` -> `71`) written in the same second
        # the previous run compiled the original was imported as the ORIGINAL
        # -- a false SURVIVED, found on CI on 2026-09-13. With the prefix, no
        # run can read bytecode another run wrote.
        env = {**os.environ, "PYTHONPYCACHEPREFIX": str(Path(scratch) / "pycache")}

        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *args, *_ADDOPTS, f"--junitxml={report}"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        outcomes = read_report(report) if report.is_file() else None

    return RunResult(exit_code=completed.returncode, outcomes=outcomes)


def _git_clean(repo: Path, file: str) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", file],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )

    return status.stdout.strip() == ""


def run_battery(
    repo: Path,
    pytest_args: Sequence[str],
    mutations: Sequence[Mutation],
    runner: Runner,
) -> tuple[RunResult, list[MutationResult]]:
    dirty = sorted({m.file for m in mutations if not _git_clean(repo, m.file)})

    if dirty:
        raise RuntimeError(f"refusing to mutate files with uncommitted changes: {dirty}")

    control = runner(pytest_args)

    executed = (
        [o for o in control.outcomes if o.status is Status.PASSED] if control.outcomes else []
    )

    if control.exit_code != 0 or not executed:
        raise RuntimeError(
            f"control run did not pass cleanly (exit {control.exit_code}, "
            f"{len(executed)} tests passed) -- no mutation verdict would mean anything"
        )

    results = []

    for mutation in mutations:
        path = repo / mutation.file
        original = path.read_bytes()
        text = original.decode("utf-8")

        matches = text.count(mutation.old)

        if matches != 1:
            results.append(
                MutationResult(mutation, Verdict.ERROR, f"`old` matches {matches} times, not once")
            )
            continue

        try:
            path.write_bytes(text.replace(mutation.old, mutation.new).encode("utf-8"))
            result = runner(pytest_args)
        finally:
            path.write_bytes(original)

        if path.read_bytes() != original or not _git_clean(repo, mutation.file):
            raise RuntimeError(f"{mutation.file} was not restored after {mutation.name!r}")

        verdict, detail = classify(result, mutation.expect_failing)

        failed = tuple(o.test_id for o in (result.outcomes or ()) if o.status is Status.FAILED)

        results.append(MutationResult(mutation, verdict, detail, failed))

    return control, results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a mutation battery from a JSON spec.")
    parser.add_argument("spec", type=Path)
    args = parser.parse_args(argv)

    backend = Path.cwd()
    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=backend,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    pytest_args, mutations = load_spec(args.spec)

    control, results = run_battery(
        repo,
        pytest_args,
        mutations,
        lambda run_args: run_pytest(run_args, cwd=backend),
    )

    passed = sum(1 for o in control.outcomes or () if o.status is Status.PASSED)
    print(f"control: exit {control.exit_code}, {passed} tests passed")

    for item in results:
        print(f"{item.verdict.value:<12} {item.mutation.name}  -- {item.detail}")

    caught = sum(1 for item in results if item.verdict is Verdict.CAUGHT)
    print(f"{caught}/{len(results)} caught")

    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
