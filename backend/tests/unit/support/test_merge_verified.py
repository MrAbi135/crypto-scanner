"""`scripts/merge_verified.py`'s decision, tested on every refusal it can give."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "merge_verified.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_verified", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check(name: str, bucket: str = "pass") -> dict[str, str]:
    return {"name": name, "bucket": bucket, "state": "SUCCESS" if bucket == "pass" else "FAILURE"}


def test_all_required_checks_passing_is_the_only_go() -> None:
    mv = _load()

    assert mv.problems([_check("backend"), _check("integration"), _check("docker")]) == []


def test_a_missing_required_check_blocks_even_when_everything_else_is_green() -> None:
    """A workflow that stopped running the integration job reads as all-green."""

    mv = _load()

    assert mv.problems([_check("backend"), _check("docker")]) == [
        "required check 'integration' did not run"
    ]


def test_a_pending_or_failing_check_blocks() -> None:
    mv = _load()

    blocking = mv.problems(
        [_check("backend"), _check("integration", "pending"), _check("x", "fail")]
    )

    assert len(blocking) == 2
    assert any("'integration' is pending" in item for item in blocking)
    assert any("'x' is fail" in item for item in blocking)


def test_no_checks_at_all_blocks() -> None:
    mv = _load()

    assert mv.problems([]) == ["no checks reported for the head commit"]
