"""Merge a pull request only after CI has verified the exact commit being merged.

The working agreement is "merge on green". PR #252 showed the gap in how that
was carried out: the merge was chained behind a shell check that looked for the
word "passed" in a log, and it went ahead before the new tests had been seen
among the passes. Two things close that:

1. CI's `backend` and `integration` jobs now end in `tests.support.suite_gate`,
   which fails the job if any test module contributed no passing test or (in
   those jobs) anything was skipped. Green now *includes* "the tests ran".
2. This script is the only merge path. It waits for every check, requires the
   two test jobs by name (a workflow that silently lost one would otherwise
   read as all-green), and merges with `--match-head-commit`, so a push that
   lands between verification and merge is refused rather than merged
   unverified.

    uv run --project backend python scripts/merge_verified.py 253 [--dry-run]

Python rather than a .sh/.ps1 pair: one file behaves the same in Git Bash and
PowerShell.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence

REQUIRED_CHECKS = ("backend", "integration")


def problems(
    checks: Sequence[dict[str, str]], required: Sequence[str] = REQUIRED_CHECKS
) -> list[str]:
    """Everything that stands between these checks and a merge. Empty means go."""

    found: list[str] = []

    if not checks:
        return ["no checks reported for the head commit"]

    names = {check["name"] for check in checks}

    for name in required:
        if name not in names:
            found.append(f"required check {name!r} did not run")

    for check in checks:
        if check.get("bucket") != "pass":
            found.append(f"check {check['name']!r} is {check.get('bucket')} ({check.get('state')})")

    return found


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # A fixed `gh` argv built here, never from untrusted input; `gh` is found
    # on PATH by design, the same way the developer runs it by hand.
    return subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge a PR only when CI verified its head commit."
    )
    parser.add_argument("pr")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    view = json.loads(_gh("pr", "view", args.pr, "--json", "headRefOid,state").stdout)

    if view["state"] != "OPEN":
        _say(f"PR {args.pr} is {view['state']}, not OPEN")
        return 1

    head = view["headRefOid"]

    # Blocks until every check has finished; its own exit code is not trusted,
    # the JSON below is what decides.
    _gh("pr", "checks", args.pr, "--watch", "--interval", "20", check=False)

    checks = json.loads(_gh("pr", "checks", args.pr, "--json", "name,bucket,state").stdout)

    blocking = problems(checks)

    if blocking:
        _say(f"NOT merging PR {args.pr} at {head[:12]}:")
        for item in blocking:
            _say(f"  - {item}")
        return 1

    summary = ", ".join(sorted(check["name"] for check in checks))
    _say(f"verified PR {args.pr} at {head[:12]}: all checks pass ({summary})")

    if args.dry_run:
        _say("dry run: not merging")
        return 0

    merged = _gh(
        "pr",
        "merge",
        args.pr,
        "--squash",
        "--delete-branch",
        "--match-head-commit",
        head,
        check=False,
    )

    _say(merged.stdout.strip() or merged.stderr.strip())

    return merged.returncode


if __name__ == "__main__":
    sys.exit(main())
