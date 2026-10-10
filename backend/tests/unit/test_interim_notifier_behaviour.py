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
from decimal import Decimal
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


def _signal(
    signal_id: str,
    grade: str = "A",
    tier: str = "T1",
    timeframe: str = "H1",
    ttl_candles: str = "12",
    age_seconds: str = "3660",
) -> dict[str, str]:
    """A row as `published_since` returns it, defaulted to a signal at FIRST SIGHT.

    `age_seconds` defaults to 3660 -- one H1 period plus a minute -- because
    that is the youngest such a row can ever be. `published_at` is the candle's
    OPEN time, so a signal does not exist until it is already a full period
    old. Defaulting this to something smaller would make every test here run
    against a row production cannot produce, which is how the five-minute
    window survived review.
    """
    return {
        "signal_id": signal_id,
        "symbol": "BTCUSDT",
        "timeframe": timeframe,
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
        "ttl_candles": ttl_candles,
        "age_seconds": age_seconds,
        # A pool-exit signal: no ladder, sealed under the zone rule.
        "target_bands": '{"primary":{"low":"110","high":"110","pool_id":"p1"},"secondary":null}',
        "invalidation_rule": "zone_distal_edge",
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


def test_the_scan_bound_clears_a_whole_candle_on_every_signal_timeframe(notifier) -> None:
    """The structural reason the old five-minute window could never fire.

    `published_at` is the candle's OPEN time, so a signal is already one full
    period old the first instant it exists. Any bound shorter than a period is
    therefore unreachable on that timeframe -- which is what 300s was against
    M5's 300s, by seventeen seconds, measured live on 2026-10-05.
    """
    bound = notifier.LOOKBACK.total_seconds()

    for timeframe in ("M5", "M15", "H1", "H4"):
        period = notifier.TIMEFRAME_SECONDS[timeframe]

        assert bound > period, (
            f"a signal on {timeframe} is {period}s old at first sight and the "
            f"scan bound is {bound}s, so it can never be seen"
        )

    assert bound >= 2 * notifier.TIMEFRAME_SECONDS["H4"], (
        "the bound must leave room for a late pass on the slowest signal "
        "timeframe, not merely equal one period"
    )


def test_the_measured_317_second_signal_is_dispatched(notifier, monkeypatch) -> None:
    """The exact row the old window dropped, as a regression test.

    SUIUSDT M5, `published_at` 04:20:00Z, written by a pass that finished at
    04:25:17.1Z: an age of 317s against a 300s window. It sat in the table all
    day and this file never logged a word about it.
    """
    _offline(notifier, monkeypatch, [])
    notifier.main()

    _offline(
        notifier,
        monkeypatch,
        [_signal("SIG_317", timeframe="M5", age_seconds="317", ttl_candles="12")],
    )
    notifier.main()

    log = notifier.LOG.read_text(encoding="utf-8")

    assert "DRY RUN would send signal SIG_317" in log, (
        "the 317-second signal was dropped again; log was: " + log
    )


def test_a_signal_past_its_own_ttl_is_suppressed_and_said_so(notifier, monkeypatch) -> None:
    """§10.2 at dispatch. A 24h bound must not push what expired hours ago.

    §10.1's "honest suppression, never silent" is why this asserts the log line
    and not merely the absence of a send.
    """
    _offline(notifier, monkeypatch, [])
    notifier.main()

    # H1, ttl 2 candles = 7200s of relevance, seen at 20000s.
    _offline(
        notifier,
        monkeypatch,
        [_signal("SIG_STALE", ttl_candles="2", age_seconds="20000")],
    )
    notifier.main()

    log = notifier.LOG.read_text(encoding="utf-8")

    assert "DRY RUN would send signal SIG_STALE" not in log, "an expired signal was pushed"
    assert "suppressed stale" in log, "it was dropped without saying why: " + log
    assert "ttl=7200s" in log, "the log must show the lifetime it was measured against"


def test_an_absent_ttl_falls_back_to_two_candles_not_to_forever(notifier) -> None:
    """A null or zero `ttl_candles` must not read as immortal."""
    period = notifier.TIMEFRAME_SECONDS["H1"]

    for absent in ("0", "", "None"):
        row = _signal("SIG_X", ttl_candles=absent)

        assert notifier.dispatch_lifetime_seconds(row) == 2 * period, (
            f"ttl_candles={absent!r} did not fall back to two candles"
        )

    assert notifier.dispatch_lifetime_seconds(_signal("SIG_X", ttl_candles="12")) == 12 * period


def _select_columns(sql: str) -> list[str]:
    """The top-level expressions of the SELECT list, split on depth-0 commas."""
    body = sql.split("select", 1)[1].split("from detection.signals", 1)[0]

    parts: list[str] = []
    depth = 0
    current = ""

    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1

        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char

    if current.strip():
        parts.append(current.strip())

    return parts


def test_every_selected_column_is_given_a_name(notifier, monkeypatch) -> None:
    """`dict(zip(...))` truncates in silence, so the two lists must be pinned together.

    Adding a column to the SELECT without adding its name here does not raise:
    `zip` stops at the shorter list, so the new column is simply discarded and
    every field after it keeps working. That is invisible in every other test,
    because they all bypass this query entirely.
    """
    captured: dict[str, str] = {}

    def fake_psql(sql: str) -> list[tuple[str, ...]]:
        captured["sql"] = sql

        return []

    monkeypatch.setattr(notifier, "_psql", fake_psql)

    notifier.published_since(NOW)

    columns = _select_columns(captured["sql"])

    assert len(columns) == 17, f"the SELECT list changed shape: {columns}"
    assert len(columns) == len(_signal("SIG_X")), (
        f"{len(columns)} columns selected but the row fixture has "
        f"{len(_signal('SIG_X'))} keys -- zip would drop the difference in silence"
    )
    assert "s.ttl_candles" in columns, "the TTL test has nothing to read"
    assert "s.target_bands" in columns, "the ladder message has nothing to read"
    assert any("epoch" in column for column in columns), (
        "the age is computed by postgres on purpose: one clock for both sides "
        "of the subtraction, and no timestamp parsing in this file"
    )


def test_an_unknown_timeframe_raises_instead_of_guessing_a_lifetime(notifier) -> None:
    """Divergence between the scanned set and the map is a defect, not a default.

    Guessing is silently wrong forever in whichever direction it guesses: too
    short suppresses every signal on that timeframe, too long pushes expired
    ones. Raising puts it in the cron log on the first occurrence.
    """
    with pytest.raises(notifier.NotifierContractError):
        notifier.dispatch_lifetime_seconds(_signal("SIG_X", timeframe="M3"))

    assert set(notifier.TIMEFRAME_SECONDS) == {"M5", "M15", "H1", "H4", "D1", "W1"}, (
        "SLS §0.2's scanned set changed; this map and the engine's config have diverged"
    )


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


# --- SLS v1.0.15: the M5 ladder in the message ---------------------------------

LADDER = '{"start_r":"2","step_r":"1","unbounded":true}'


def _laddered(direction: str = "UP", rule: str = "risk_stop") -> dict[str, str]:
    """The live M5 shape: zone 100-104, a 1% risk stop. numeric(38,18) text,
    as psql returns it, so trailing zeros are part of what is tested."""
    row = _signal("SIG_L", timeframe="M5", ttl_candles="48", age_seconds="360")

    if direction == "UP":
        proximal, distal, stop = "104", "100", "102.96"
    else:
        proximal, distal, stop = "100", "104", "101"

    def wide(value: str) -> str:
        return f"{Decimal(value):.18f}"

    row.update(
        direction=direction,
        entry_proximal=wide(proximal),
        entry_distal=wide(distal),
        invalidation=wide(stop) if rule == "risk_stop" else wide(distal),
        target_bands='{"primary":{"low":"120","high":"120","pool_id":"p1"},"secondary":null,'
        f'"ladder":{LADDER}}}',
        invalidation_rule=rule,
    )

    return row


@pytest.mark.parametrize("direction", ["UP", "DOWN"])
@pytest.mark.parametrize("rule", ["risk_stop", "zone_distal_edge"])
def test_the_notifier_prices_the_rungs_as_the_domain_does(notifier, direction, rule) -> None:
    """The one copy of the R arithmetic outside the domain, held to it.

    The notifier is standard-library only and cannot import `SignalLevels`, so
    it restates the anchor rule (proximal for a risk stop, mid otherwise) and
    the ladder. A drift would put a TP price on a phone that the engine never
    judges against -- so every combination is compared, not one example.
    """
    from scanner.domain.confluence import SignalLevels, TargetBand, TargetLadder, entry_zone
    from scanner.domain.confluence.levels import Invalidation

    row = _laddered(direction, rule)

    zone = entry_zone(
        zone_id="z1",
        direction=direction,
        band_low=Decimal(100),
        band_high=Decimal(104),
    )
    levels = SignalLevels(
        direction=direction,
        entry=zone,
        invalidation=Invalidation(Decimal(row["invalidation"]), rule),
        primary_target=TargetBand(low=Decimal(120), high=Decimal(120), pool_id="p1"),
        ladder=TargetLadder(Decimal(2), Decimal(1)),
    )

    rungs = notifier.rung_prices(row, notifier.ladder_of(row))

    assert [n for n, _, _ in rungs] == [1, 2, 3]
    for n, r, price in rungs:
        assert r == levels.ladder.rung_r(n)
        expected = levels.rung_price(n)
        assert price == expected, f"TP{n}: notifier {price}, domain {expected}"


def test_a_laddered_signal_is_announced_with_a_touch_stop_and_its_tps(notifier) -> None:
    text = notifier.format_signal(_laddered())

    assert "stop 102.960000000000000000 (touch, not close)" in text
    assert "TP1 106.08 (2R) · TP2 107.12 (3R) · TP3 108.16 (4R) · no cap" in text
    assert "trails" in text
    assert "close beyond" not in text, "the pool-exit rule would tell the trader the opposite"


def test_a_pool_exit_signal_is_announced_as_before(notifier) -> None:
    text = notifier.format_signal(_signal("SIG_P"))

    assert "invalidation 99 (close beyond, not touch)" in text
    assert "TP1" not in text


def test_an_unreadable_target_column_falls_back_to_the_pool_message(notifier) -> None:
    """A malformed column must cost the TP line, never the alert."""
    row = _signal("SIG_B")
    row["target_bands"] = "not json"

    assert "close beyond" in notifier.format_signal(row)


def test_a_close_the_loop_message_carries_the_booked_r(notifier) -> None:
    row = {
        "transition_id": "t-1",
        "signal_id": "SIG_L",
        "to_state": "CLOSED_FLAT",
        "at": NOW.isoformat(),
        "trigger_evidence": '{"rungs":"1","realised_r":"0"}',
    }

    assert notifier.format_transition(row).endswith("realised 0R")

    row["trigger_evidence"] = '{"rungs":"2","realised_r":"2.000000000000000000"}'
    assert notifier.format_transition(row).endswith("realised 2R")


def test_a_pool_exit_close_carries_no_r(notifier) -> None:
    row = {
        "transition_id": "t-2",
        "signal_id": "SIG_P",
        "to_state": "SUCCESS",
        "at": NOW.isoformat(),
        "trigger_evidence": "{}",
    }

    assert "realised" not in notifier.format_transition(row)
