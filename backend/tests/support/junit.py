"""Read a pytest JUnit XML report into per-test outcomes.

The mutation battery and the CI suite gate both need to know what a pytest run
actually did, and both used to find out by reading its terminal output. That
broke twice on the same cause: `pyproject.toml` already passes `-q`, a caller
that added its own `-q` made it `-qq`, and `-qq` prints no summary line at all
-- so a parser looking for "1 failed" found nothing and reported a verdict
the run never gave. The terminal is for people. This reads the report pytest
writes for programs.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"  # an assertion failed inside the test
    ERROR = "error"  # setup/teardown or collection broke: not a test verdict
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Outcome:
    module: str
    name: str
    status: Status

    @property
    def test_id(self) -> str:
        return f"{self.module}::{self.name}" if self.name else self.module


def module_of(classname: str, name: str) -> str:
    """The dotted test module an entry belongs to.

    Two shapes reach here. A test records its module (and any class) in
    `classname`: `tests.x.test_mod` or `tests.x.test_mod.TestGroup`. A whole
    module that was skipped or failed at collection has an empty `classname`
    and carries the module in `name` instead.
    """
    dotted = classname or name
    parts = dotted.split(".")

    for index in range(len(parts) - 1, -1, -1):
        if parts[index].startswith("test_"):
            return ".".join(parts[: index + 1])

    return dotted


def read_report(path: Path) -> tuple[Outcome, ...]:
    root = ET.parse(path).getroot()

    outcomes = []

    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")

        if case.find("failure") is not None:
            status = Status.FAILED
        elif case.find("error") is not None:
            status = Status.ERROR
        elif case.find("skipped") is not None:
            status = Status.SKIPPED
        else:
            status = Status.PASSED

        collection_level = not classname

        outcomes.append(
            Outcome(
                module=module_of(classname, name),
                name="" if collection_level else name,
                status=status,
            )
        )

    return tuple(outcomes)
