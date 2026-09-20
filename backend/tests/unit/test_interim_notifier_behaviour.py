"""The interim notifier's decisions, pinned. **Deleted at S18 with the notifier.**

`ops/notify/` is not application code and does not ship in the image, so it sits
outside `src/` and outside the coverage floor. It still decides what reaches a
phone, and the first version of it had a defect that only running it showed: a
cold start with no signals in the window wrote no ledger at all, so every later
run cold-started again and the first real signal to arrive would have been
marked seen instead of sent. At one signal a fortnight that fault is invisible
for weeks and then eats the one message the tool exists to deliver.

These tests cover the decisions, not the delivery: the database and Telegram are
never touched. What is pinned is the ledger's arithmetic, because that is what
decides whether something is sent twice, never, or once.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

NOTIFIER = Path(__file__).resolve().parents[3] / "ops" / "notify" / "notify_signals.py"

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture()
def notifier(tmp_path: Path):
    """The module, with its on-disk paths redirected into a temp directory.

    Imported by path rather than installed: `ops/` is not a package and making
    it one, to be imported by a test that deletes itself at S18, would leave a
    permanent mark for a temporary thing.
    """
    spec = importlib.util.spec_from_file_location("interim_notifier", NOTIFIER)

    assert spec is not None and spec.loader is not None, f"cannot load {NOTIFIER}"

    module = importlib.util.module_from_spec(spec)
    sys.modules["interim_notifier"] = module
    spec.loader.exec_module(module)

    module.LEDGER = tmp_path / "notified.txt"
    module.ATTEMPTS = tmp_path / "attempts.tsv"
    module.LOG = tmp_path / "notify.log"

    yield module

    del sys.modules["interim_notifier"]


def _offline(notifier, monkeypatch, rows: list[dict[str, str]]) -> None:
    """Run `main()` without a database, a telegram or a real clock.

    These tests must drive `main()` itself. An earlier draft asserted against
    `ledger_append` directly, which writes the file unconditionally and so
    passed against the very defect it was written for -- a check that cannot
    fail, which is the failure mode this repository keeps finding.
    """
    monkeypatch.setenv("SEND", "0")
    monkeypatch.setattr(notifier, "assert_tier_readable", lambda: None)
    monkeypatch.setattr(notifier, "published_since", lambda cutoff: rows)
    monkeypatch.setattr(
        notifier,
        "_psql",
        lambda sql: pytest.fail(f"no query should reach the database: {sql}"),
    )


def _signal(signal_id: str, grade: str = "A", tier: str = "T1") -> dict[str, str]:
    return {
        "signal_id": signal_id,
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "direction": "UP",
        "archetype": "A3",
        "grade": grade,
        "confidence": "80",
        "entry_proximal": "100",
        "entry_distal": "99",
        "invalidation": "99",
        "published_at": NOW.isoformat(),
        "tier": tier,
        "condition_tags": "[]",
    }


def test_a_cold_start_with_no_signals_still_ends_the_cold_start(notifier, monkeypatch) -> None:
    """The defect that running it found, driven through `main()`.

    A quiet first run is the normal one at a signal a fortnight. If it leaves
    no ledger the cold start never ends, and the next run -- the one that
    finally has a signal -- treats that signal as history.
    """
    _offline(notifier, monkeypatch, [])

    assert notifier.main() == 0
    assert notifier.LEDGER.exists(), "a cold start must leave a ledger even with nothing to mark"

    _, pushed = notifier.load_ledger()

    assert pushed == set(), "the marker must not look like something that was sent"
    assert notifier.ledger_count_today(NOW) == 0, "the marker must not consume the daily cap"


def test_the_first_signal_after_a_cold_start_is_not_swallowed(notifier, monkeypatch) -> None:
    """The consequence, end to end: cold start on nothing, then a real signal.

    With the defect the second run cold-starts again and files the signal as
    history; the log says "marked seen, none sent" and looks entirely healthy.
    """
    _offline(notifier, monkeypatch, [])
    assert notifier.main() == 0

    _offline(notifier, monkeypatch, [_signal("SIG_FIRST_EVER")])
    assert notifier.main() == 0

    lines = notifier.LOG.read_text(encoding="utf-8").splitlines()

    assert any("DRY RUN would send signal SIG_FIRST_EV" in line for line in lines), (
        "the first signal after a cold start was swallowed; log was:\n" + "\n".join(lines)
    )
    assert sum("cold start" in line for line in lines) == 1, (
        "the cold start must happen once, not on every quiet run"
    )


def test_grade_and_tier_caps_are_applied_through_main(notifier, monkeypatch) -> None:
    """§10.1, driven the same way: B never pushes and an ineligible tier never does."""

    _offline(notifier, monkeypatch, [])
    notifier.main()

    _offline(
        notifier,
        monkeypatch,
        [
            _signal("SIG_GRADE_B", grade="B"),
            _signal("SIG_INELIGIBLE", tier="INELIGIBLE"),
            _signal("SIG_GOOD"),
        ],
    )
    notifier.main()

    lines = notifier.LOG.read_text(encoding="utf-8")

    assert "suppressed grade=B" in lines
    assert "suppressed tier=INELIGIBLE" in lines
    assert "DRY RUN would send signal SIG_GOOD" in lines


def test_a_signal_seen_once_is_never_reconsidered(notifier) -> None:
    """Whatever the verdict was. Re-deciding writes the same line every minute."""

    for kind in ("signal", "suppressed-grade", "suppressed-tier", "suppressed-cap", "failed"):
        notifier.ledger_append(NOW, kind, f"SIG_{kind}")

    decided, _ = notifier.load_ledger()

    for kind in ("signal", "suppressed-grade", "suppressed-tier", "suppressed-cap", "failed"):
        assert f"SIG_{kind}" in decided


def test_only_a_pushed_signal_earns_a_close_the_loop_message(notifier) -> None:
    """§10.3 exempts the outcome from cooldown -- of a signal that was announced.

    Reporting the stop of an entry nobody was told about is noise wearing the
    costume of diligence.
    """
    notifier.ledger_append(NOW, "signal", "SIG_SENT")
    notifier.ledger_append(NOW, "suppressed-grade", "SIG_DROPPED")

    _, pushed = notifier.load_ledger()

    assert pushed == {"SIG_SENT"}


def test_transitions_are_namespaced_apart_from_signals(notifier) -> None:
    """Both are ids in one file; only one of them is a signal id."""

    notifier.ledger_append(NOW, "transition", "TR_1")

    decided, pushed = notifier.load_ledger()

    assert "transition:TR_1" in decided
    assert "TR_1" not in pushed


def test_the_daily_cap_counts_deliveries_and_nothing_else(notifier) -> None:
    """§10.1's cap rations attention. A rejection spends none of it."""

    for kind in ("signal", "transition", "suppressed-grade", "failed", "coldstart"):
        notifier.ledger_append(NOW, kind, f"ID_{kind}")

    notifier.ledger_append(NOW - timedelta(days=1), "signal", "ID_yesterday")

    assert notifier.ledger_count_today(NOW) == 2


def test_the_publication_window_is_the_measured_five_minutes(notifier) -> None:
    """Two minutes was the first proposal and would have dropped signals.

    `published_at` is the candle close time, and close-to-row was at worst
    68.7s on H1 over three hours of live logs. With a one-minute cron the worst
    case age at first sight is ~129s.
    """
    assert timedelta(minutes=5) == notifier.PUBLISH_WINDOW
    assert notifier.PUBLISH_WINDOW.total_seconds() > 129


def test_the_contract_guard_refuses_the_rankings_source(notifier) -> None:
    """The second layer. The grant is the first, and the integration suite attacks it."""

    with pytest.raises(notifier.NotifierContractError):
        notifier._psql("select count(*) from detection.setups")

    with pytest.raises(notifier.NotifierContractError):
        notifier._psql("select 1 from detection.signals s join detection.setups u on true")

    with pytest.raises(notifier.NotifierContractError):
        notifier._psql("select 1")

    with pytest.raises(notifier.NotifierContractError):
        notifier._psql("delete from detection.signals")


def test_only_terminal_non_refresh_states_close_the_loop(notifier) -> None:
    """A `refresh` row is a re-detection and a `stress_test` row is a surviving wick.

    Announcing either as an outcome reports something that did not happen. The
    filter lives in the SQL, so this pins the SQL.
    """
    calls: list[str] = []

    notifier._psql = lambda sql: calls.append(sql) or []

    notifier.terminal_transitions(["SIG_1"])

    assert len(calls) == 1

    sql = " ".join(calls[0].split())

    assert "t.refresh = false" in sql
    assert "SUCCESS" in sql and "FAILED" in sql
    assert "EXPIRED_UNTOUCHED" in sql and "EXPIRED_ACTIVE" in sql
    assert "INVALIDATED_EARLY" in sql
    # There is no bare "EXPIRED" state; a filter naming one would match nothing.
    assert "'EXPIRED'" not in sql


def test_no_transitions_are_read_when_nothing_was_pushed(notifier) -> None:
    """A query per minute that can only return rows for an empty list."""

    notifier._psql = lambda sql: pytest.fail("no query should be issued")

    assert notifier.terminal_transitions([]) == []
