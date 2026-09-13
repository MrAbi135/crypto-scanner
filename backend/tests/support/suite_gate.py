"""CI gate: every test module the job is responsible for actually ran.

A green pytest exit says no collected test failed. It does not say the tests
ran. A module whose `importorskip` fails is one skipped entry; a marker typo
deselects a file; a fixture that skips on a missing service turns a suite into
dots that assert nothing. PR #252 merged on a green integration job before
anyone had confirmed its seventeen new tests were among the passes -- they
were, but the gate that said so was a person comparing two counts afterwards.

This makes it the job's own last step: read the JUnit report, list the test
files on disk the job owns, and fail if any of them contributed no passing
test -- and, where the job says so, if anything was skipped at all.

    python -m tests.support.suite_gate --junit reports/integration.xml \
        --modules "tests/integration/test_*.py" --forbid-skips
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tests.support.junit import Outcome, Status, read_report


@dataclass(frozen=True, slots=True)
class GateReport:
    required: tuple[str, ...]
    silent_modules: tuple[str, ...]
    skipped: tuple[str, ...]
    passed: int
    forbid_skips: bool

    @property
    def ok(self) -> bool:
        return not self.silent_modules and not (self.forbid_skips and self.skipped)


def dotted(path: Path) -> str:
    return ".".join(path.with_suffix("").parts)


def required_modules(
    root: Path,
    include: Sequence[str],
    exclude: Sequence[str] = (),
) -> tuple[str, ...]:
    found = set()

    for pattern in include:
        for path in root.glob(pattern):
            relative = path.relative_to(root)
            posix = relative.as_posix()

            if any(fnmatch.fnmatch(posix, skip) for skip in exclude):
                continue

            found.add(dotted(relative))

    return tuple(sorted(found))


def evaluate(
    outcomes: Iterable[Outcome],
    required: Sequence[str],
    *,
    forbid_skips: bool,
) -> GateReport:
    outcomes = tuple(outcomes)

    ran = {o.module for o in outcomes if o.status is Status.PASSED}

    return GateReport(
        required=tuple(required),
        silent_modules=tuple(module for module in required if module not in ran),
        skipped=tuple(o.test_id for o in outcomes if o.status is Status.SKIPPED),
        passed=sum(1 for o in outcomes if o.status is Status.PASSED),
        forbid_skips=forbid_skips,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--modules", action="append", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--forbid-skips", action="store_true")
    args = parser.parse_args(argv)

    if not args.junit.is_file():
        print(f"suite gate: no JUnit report at {args.junit} -- the test step did not run")
        return 1

    required = required_modules(Path.cwd(), args.modules, args.exclude)

    if not required:
        print(f"suite gate: no test files match {args.modules} -- the gate would check nothing")
        return 1

    report = evaluate(read_report(args.junit), required, forbid_skips=args.forbid_skips)

    print(
        f"suite gate: {len(report.required)} modules required, {report.passed} tests passed, "
        f"{len(report.skipped)} skipped"
    )

    for module in report.silent_modules:
        print(f"  NO PASSING TEST: {module}")

    if report.forbid_skips:
        for test_id in report.skipped:
            print(f"  SKIPPED (forbidden in this job): {test_id}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
