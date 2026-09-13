"""The instruments that say whether tests ran and whether a mutation was caught.

Both exist because the previous versions of them reported things that were
not true -- a battery that printed SURVIVED for mutations its own failure
list showed were caught, and a merge that went ahead before anyone had seen
the new tests among the passes. An instrument that cannot report the bad
outcome is decoration, so every verdict each of them can give is produced
here at least once, including the ones nobody hopes to see.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.support import mutation_battery as mb
from tests.support import suite_gate
from tests.support.junit import Outcome, Status, module_of, read_report

# The shapes pytest actually writes, taken from a probe run: a module skipped
# at collection (empty classname), a class-based test, a parametrised id, and
# failure versus setup error.
_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="" name="tests.probe.test_skipped_module" time="0.000">
  <skipped message="collection skipped">importorskip</skipped></testcase>
<testcase classname="tests.probe.test_mixed" name="test_pass" time="0.002" />
<testcase classname="tests.probe.test_mixed" name="test_fail" time="0.002">
  <failure message="assert 1 == 2">assert</failure></testcase>
<testcase classname="tests.probe.test_mixed" name="test_skip" time="0.001">
  <skipped type="pytest.skip" message="x">x</skipped></testcase>
<testcase classname="tests.probe.test_mixed" name="test_error_fixture" time="0.001">
  <error message="failed on setup">fixture</error></testcase>
<testcase classname="tests.probe.test_mixed.TestGroup" name="test_in_class" time="0.001" />
<testcase classname="tests.probe.test_mixed" name="test_param[a-b]" time="0.003" />
</testsuite></testsuites>
"""


@pytest.fixture()
def report(tmp_path: Path) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(_REPORT, encoding="utf-8")
    return path


# --------------------------------------------------------------------- junit


def test_every_status_is_read_from_its_own_element(report: Path) -> None:
    outcomes = {o.test_id: o.status for o in read_report(report)}

    assert outcomes == {
        "tests.probe.test_skipped_module": Status.SKIPPED,
        "tests.probe.test_mixed::test_pass": Status.PASSED,
        "tests.probe.test_mixed::test_fail": Status.FAILED,
        "tests.probe.test_mixed::test_skip": Status.SKIPPED,
        "tests.probe.test_mixed::test_error_fixture": Status.ERROR,
        "tests.probe.test_mixed::test_in_class": Status.PASSED,
        "tests.probe.test_mixed::test_param[a-b]": Status.PASSED,
    }


@pytest.mark.parametrize(
    ("classname", "name", "module"),
    [
        ("tests.a.test_x", "test_y", "tests.a.test_x"),
        ("tests.a.test_x.TestGroup", "test_y", "tests.a.test_x"),
        ("", "tests.a.test_x", "tests.a.test_x"),
    ],
)
def test_the_module_is_found_for_functions_classes_and_collection_entries(
    classname: str, name: str, module: str
) -> None:
    assert module_of(classname, name) == module


# ---------------------------------------------------------------- suite gate


def test_a_module_that_only_skipped_is_reported_silent(report: Path) -> None:
    gate = suite_gate.evaluate(
        read_report(report),
        ["tests.probe.test_mixed", "tests.probe.test_skipped_module"],
        forbid_skips=False,
    )

    assert gate.silent_modules == ("tests.probe.test_skipped_module",)
    assert not gate.ok


def test_a_required_module_absent_from_the_report_is_silent(report: Path) -> None:
    gate = suite_gate.evaluate(
        read_report(report), ["tests.probe.test_mixed", "tests.probe.test_new"], forbid_skips=False
    )

    assert gate.silent_modules == ("tests.probe.test_new",)


def test_skips_fail_the_gate_only_where_the_job_forbids_them(report: Path) -> None:
    outcomes = read_report(report)

    allowed = suite_gate.evaluate(outcomes, ["tests.probe.test_mixed"], forbid_skips=False)
    forbidden = suite_gate.evaluate(outcomes, ["tests.probe.test_mixed"], forbid_skips=True)

    assert allowed.ok
    assert not forbidden.ok
    assert set(forbidden.skipped) == {
        "tests.probe.test_skipped_module",
        "tests.probe.test_mixed::test_skip",
    }


def test_required_modules_come_from_disk_minus_exclusions(tmp_path: Path) -> None:
    for relative in ("tests/unit/test_a.py", "tests/integration/test_b.py", "tests/unit/helper.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    assert suite_gate.required_modules(
        tmp_path, ["tests/**/test_*.py"], ["tests/integration/*"]
    ) == ("tests.unit.test_a",)


def test_the_gate_fails_when_there_is_no_report_or_nothing_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert suite_gate.main(["--junit", "missing.xml", "--modules", "tests/*.py"]) == 1

    (tmp_path / "r.xml").write_text(_REPORT, encoding="utf-8")

    assert suite_gate.main(["--junit", "r.xml", "--modules", "tests/test_*.py"]) == 1


# ----------------------------------------------------------------- classify


def _outcome(name: str, status: Status) -> Outcome:
    return Outcome(module="tests.test_m", name=name, status=status)


@pytest.mark.parametrize(
    ("exit_code", "outcomes", "verdict"),
    [
        (0, (_outcome("test_target", Status.PASSED),), mb.Verdict.SURVIVED),
        (1, (_outcome("test_target", Status.FAILED),), mb.Verdict.CAUGHT),
        (1, (_outcome("test_other", Status.FAILED),), mb.Verdict.MISDIRECTED),
        # The named test crashed in setup: not an assertion catching the bug.
        (1, (_outcome("test_target", Status.ERROR),), mb.Verdict.ERROR),
        (2, (), mb.Verdict.ERROR),
        (5, (), mb.Verdict.ERROR),
        (1, None, mb.Verdict.ERROR),
    ],
)
def test_every_verdict_follows_from_exit_code_and_report(
    exit_code: int, outcomes: tuple[Outcome, ...] | None, verdict: mb.Verdict
) -> None:
    result = mb.RunResult(exit_code=exit_code, outcomes=outcomes)

    assert mb.classify(result, ["test_target"])[0] is verdict


# ------------------------------------------------------------- the battery


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway repository with one module and one test of it."""

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        textwrap.dedent(
            """
            from calc import add

            def test_add():
                assert add(2, 2) == 4

            def test_other():
                assert True
            """
        ),
        encoding="utf-8",
    )

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "battery@test")
    _git(tmp_path, "config", "user.name", "battery")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "fixture")

    return tmp_path


def _mutation(name: str, old: str, new: str, expect: str = "test_add") -> mb.Mutation:
    return mb.Mutation(name=name, file="calc.py", old=old, new=new, expect_failing=(expect,))


def test_a_real_pytest_run_yields_each_verdict_and_restores_the_file(repo: Path) -> None:
    """End to end, through a real pytest subprocess and a real JUnit report.

    This is the case the hand-rolled batteries got wrong: no terminal output is
    read anywhere, so no verbosity flag can change the verdict.
    """
    original = (repo / "calc.py").read_bytes()

    control, results = mb.run_battery(
        repo,
        ["test_calc.py"],
        [
            _mutation("subtracts", "a + b", "a - b"),
            _mutation("comment only", "return a + b", "return a + b  # same"),
            _mutation("caught by the wrong test", "a + b", "a - b", expect="test_other"),
            _mutation("breaks the import", "return a + b", "return a +"),
            _mutation("pattern absent", "a * b", "a / b"),
        ],
        lambda args: mb.run_pytest(args, cwd=repo),
    )

    assert control.exit_code == 0

    assert [r.verdict for r in results] == [
        mb.Verdict.CAUGHT,
        mb.Verdict.SURVIVED,
        mb.Verdict.MISDIRECTED,
        mb.Verdict.ERROR,
        mb.Verdict.ERROR,
    ]
    assert (repo / "calc.py").read_bytes() == original


def test_a_same_length_edit_in_the_same_second_is_not_read_from_stale_bytecode(
    repo: Path,
) -> None:
    """The defect CI found on 2026-09-13, reproduced without relying on timing.

    A `.pyc` is trusted when the source's mtime (whole seconds) and size match
    what was recorded at compile time. `a + b` -> `a - b` keeps the size, and
    on a fast machine the mutation lands in the same second as the control run
    that compiled the original -- so the mutated run imported the old bytecode,
    the test passed against code that no longer existed, and the verdict read
    SURVIVED. Here the mtime is put back by hand, which is exactly that second.
    """
    source = repo / "calc.py"
    before = source.stat()

    assert mb.run_pytest(["test_calc.py"], cwd=repo).exit_code == 0

    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert source.stat().st_size == before.st_size

    mutated = mb.run_pytest(["test_calc.py"], cwd=repo)

    assert mutated.exit_code == 1, "the run imported bytecode compiled from the original source"


def test_every_run_gets_its_own_hypothesis_example_database(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A counterexample saved by one run must not reach the next.

    Hypothesis stores every failing example it finds and replays it first on
    the next run of the same test. Under a battery that meant the first mutated
    run's discovery was replayed by every later run, so a property that only
    sometimes finds a bug read as catching it every time -- a verdict about the
    example database, not about the property. CI starts with no database, so
    no battery run may inherit one either.
    """
    (repo / "test_env.py").write_text(
        "import os\n"
        "import pathlib\n"
        "\n"
        "\n"
        "def test_record():\n"
        "    pathlib.Path(os.environ['PROBE_OUT']).write_text(\n"
        "        os.environ.get('HYPOTHESIS_STORAGE_DIRECTORY', ''), encoding='utf-8'\n"
        "    )\n",
        encoding="utf-8",
    )

    stores = []

    for run in ("first", "second"):
        out = tmp_path / f"{run}.txt"
        monkeypatch.setenv("PROBE_OUT", str(out))

        assert mb.run_pytest(["test_env.py"], cwd=repo).exit_code == 0

        stores.append(out.read_text(encoding="utf-8"))

    assert all(stores), "a run inherited the default Hypothesis storage directory"
    assert stores[0] != stores[1], "two runs shared one Hypothesis example database"
    assert not (repo / ".hypothesis").exists(), "a run wrote to the repository's .hypothesis"


def test_a_failing_control_run_stops_the_battery(repo: Path) -> None:
    failing = mb.RunResult(exit_code=1, outcomes=(_outcome("test_add", Status.FAILED),))

    with pytest.raises(RuntimeError, match="control run"):
        mb.run_battery(repo, [], [_mutation("m", "a + b", "a - b")], lambda args: failing)


def test_a_control_run_that_executed_nothing_stops_the_battery(repo: Path) -> None:
    empty = mb.RunResult(exit_code=0, outcomes=())

    with pytest.raises(RuntimeError, match="control run"):
        mb.run_battery(repo, [], [_mutation("m", "a + b", "a - b")], lambda args: empty)


def test_uncommitted_work_is_never_mutated(repo: Path) -> None:
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b  # wip\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted"):
        mb.run_battery(repo, [], [_mutation("m", "a + b", "a - b")], lambda args: None)  # type: ignore[arg-type,return-value]


def test_the_file_is_restored_even_when_the_run_raises(repo: Path) -> None:
    original = (repo / "calc.py").read_bytes()
    passing = mb.RunResult(exit_code=0, outcomes=(_outcome("test_add", Status.PASSED),))
    calls = []

    def runner(args: object) -> mb.RunResult:
        calls.append((repo / "calc.py").read_text(encoding="utf-8"))

        if len(calls) == 2:
            raise KeyboardInterrupt

        return passing

    with pytest.raises(KeyboardInterrupt):
        mb.run_battery(repo, [], [_mutation("m", "a + b", "a - b")], runner)

    assert "a - b" in calls[1]
    assert (repo / "calc.py").read_bytes() == original


def test_a_spec_that_names_no_expected_test_is_refused(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        '{"pytest": [], "mutations": [{"name": "m", "file": "f", "old": "a", "new": "b",'
        ' "expect_failing": []}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expect_failing"):
        mb.load_spec(spec)


def test_the_runner_uses_this_interpreter() -> None:
    """The battery runs pytest as `sys.executable -m pytest`, so it tests the
    environment it was launched from rather than whatever is first on PATH."""

    assert Path(sys.executable).exists()
