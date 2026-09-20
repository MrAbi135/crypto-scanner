#!/usr/bin/env python3
"""INTERIM signal notifier — push published signals to Telegram until S18.

**DELETE THIS AT S18.** The alert engine (Sprint S18, SLS §10) is the real
thing: priorities, per-key cooldowns, quiet hours, daily caps, storm mode, a
delivery/suppression ledger and a fallback chain. This file has none of that.
It exists because the rate today is roughly one signal per fortnight and
checking by hand for that is silly — not because it is an alert engine.

`backend/tests/unit/test_interim_notifier_is_retired_at_s18.py` fails the
build if this directory still exists once `infrastructure/channels/` has
code, so the deletion cannot be forgotten. Drop the database role too:
`DROP ROLE scanner_notify;` (see ops/db/notify-role.sql).

What it deliberately does NOT implement, so a reader knows what is missing:

* §10.1's High/Medium split — everything pushable is pushed the same way.
* §10.3's per-key cooldowns — unnecessary at this rate, absent all the same.
* §10.2's storm circuit breaker (40 signals / 5 min). At 9 signals in the
  project's lifetime the breaker has never been near tripping; if this file
  outlives that fact, it is already overdue for deletion.
* Quiet hours, digests, per-user anything. There is one recipient.

What it does implement, because skipping these would make it lie:

* Only signals published in the last five minutes (see PUBLISH_WINDOW).
* Only grades S and A. §10.1 puts grade B on the dashboard, never on a push.
* Only tiers 1–3, which is §10.1's cap. Two symbols in the scanned set are
  INELIGIBLE (LISTAUSDT, LITEBUSDT — they are there to prove G1b's
  unselected-symbol criterion) and must never push.
* A daily ceiling, §10.1's own `user_daily_cap = 25`, and every drop is
  logged. §10.1: "honest suppression, never silent."
* Terminal state changes, which §10.3 exempts from cooldown because closing
  the loop is not noise.

Reads the database directly as `scanner_notify`, which can SELECT exactly
three things and nothing else (ops/db/notify-role.sql). The contract "never
push a candidate that skipped the §15.3 publication gates" is therefore
enforced by postgres, not by this file's good intentions — `detection.setups`,
the table behind `GET /api/v1/rankings`, answers "permission denied". The
whitelist in `_psql` is the second layer, not the first.

Runs from cron on the VM host, once a minute, outside every scanner
container. It cannot see the engine and the engine cannot see it: if this
script dies, detection does not notice.

Python 3.10 compatible (the VM host has 3.10.12) and standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Doctrine constants. Each one cites the rule it implements; a number without
# a citation here is a number somebody invented.
# --------------------------------------------------------------------------

# §15.3(2) checks freshness at the *publication* moment; §10.2 wants it at the
# *dispatch* moment. Those are different instants and nothing in the row
# reconciles them, so the window is how we keep them close: a signal dispatched
# five minutes after publication was published on a feed that was fresh five
# minutes ago, which is the most this design can honestly claim.
#
# Five, not two. `published_at` is the candle CLOSE time (confluence_replay.py
# writes `published_at=event_at`), and the row appears afterwards. Measured over
# three hours of live engine logs, close -> row written was at worst 68.7s on
# H1 and 58.2s on H4 — the only two timeframes that publish. With a one-minute
# cron the worst case age at first sight is 68.7 + 60 = ~129s, so a 120s window
# would drop signals silently. 300s leaves ~2.5x margin.
PUBLISH_WINDOW = timedelta(minutes=5)

# §10.1: High is grade S, Medium is grade A, and "Low | Grade B | No push;
# dashboard feed + optional digest".
PUSH_GRADES = frozenset({"S", "A"})

# §10.1 again: High is "Tier 1-2", Medium is "Tier 1-3". Nothing pushes above
# tier 3. Read live rather than hardcoded because tiers move — the daily
# universe job demotes and promotes symbols every midnight.
PUSH_TIERS = frozenset({"T1", "T2", "T3"})

# §10.1: "P.alert.user_daily_cap = 25 push alerts/day default".
DAILY_CAP = 25

# §12's terminal states, spelled exactly as domain/lifecycle/state.py spells
# them. Note there is no bare "EXPIRED": a TTL can lapse with the entry never
# touched or while the signal was live, and §12.4 reports those separately.
# SUPPRESSED is terminal too but never reaches a signal row — a suppressed
# candidate never becomes one.
TERMINAL_STATES = (
    "SUCCESS",
    "FAILED",
    "EXPIRED_UNTOUCHED",
    "EXPIRED_ACTIVE",
    "INVALIDATED_EARLY",
)

# --------------------------------------------------------------------------
# The contract, as capability rather than intention.
# --------------------------------------------------------------------------

# Every table this file may read. `detection.setups` is deliberately absent:
# it holds below-floor candidates that never faced §15.3 at all, and it is what
# `GET /api/v1/rankings` serves ("the board reports its own denominator" —
# rankings.py). Pushing one of those rows would alert exactly what the doctrine
# decided to suppress. `market.symbols` is here for the §10.1 tier cap and the
# grant is column-scoped to three columns.
ALLOWED_TABLES = frozenset(
    {
        "detection.signals",
        "detection.signal_transitions",
        "market.symbols",
    }
)

# Belt to the grant's braces. The role cannot execute any of these, so a match
# here means this file was edited into something it is not, and the traceback
# should say so at the call site rather than as a postgres error five frames
# down.
_WRITE_VERBS = re.compile(
    r"\b(insert|update|delete|truncate|create|drop|alter|grant|revoke|copy)\b",
    re.IGNORECASE,
)

_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([a-z_]+\.[a-z_]+)", re.IGNORECASE)

DB_CONTAINER = "scanner-dev-db-1"
DB_ROLE = "scanner_notify"
DB_NAME = "scanner"

FIELD_SEP = "\x1f"

LOG_DIR = Path.home() / "soak-logs"
LEDGER = LOG_DIR / "notified.txt"
ATTEMPTS = LOG_DIR / "notify_attempts.tsv"
LOG = LOG_DIR / "notify.log"

MAX_ATTEMPTS = 10

ENV_FILE = Path(__file__).resolve().parents[1] / "env" / "notify.local.env"


class NotifierContractError(RuntimeError):
    """Raised when this file tries to read something it promised not to."""


class NotifierSetupError(RuntimeError):
    """Raised when the environment is wrong in a way that must not be ignored."""


# --------------------------------------------------------------------------
# Database access — one choke point, and everything goes through it.
# --------------------------------------------------------------------------


def _psql(sql: str) -> list[list[str]]:
    """Run one read-only statement as `scanner_notify` and return its rows.

    The guard refuses before the query is sent. It is not the security
    boundary — the grant is — but a violation should fail here, loudly, with
    the offending table named, rather than arriving as a permission error that
    a future reader might "fix" by widening the role.
    """
    tables = {t.lower() for t in _FROM_JOIN.findall(sql)}

    if not tables:
        raise NotifierContractError(
            "no table found in the statement; the guard cannot verify what "
            "this reads, so it is refused:\n" + sql
        )

    forbidden = tables - ALLOWED_TABLES

    if forbidden:
        raise NotifierContractError(
            f"blocked: {', '.join(sorted(forbidden))}. This file may read only "
            f"{', '.join(sorted(ALLOWED_TABLES))}. detection.setups in "
            "particular holds below-floor candidates that never passed the "
            "§15.3 publication gates."
        )

    verb = _WRITE_VERBS.search(sql)

    if verb:
        raise NotifierContractError(f"blocked: this file never writes, found '{verb.group(0)}'")

    statement = "SET default_transaction_read_only = on;\n" + sql

    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        [
            "docker",
            "exec",
            "-i",
            DB_CONTAINER,
            "psql",
            "-U",
            DB_ROLE,
            "-d",
            DB_NAME,
            "-v",
            "ON_ERROR_STOP=1",
            "-X",
            "-q",
            "-t",
            "-A",
            "-F",
            FIELD_SEP,
        ],
        input=statement,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    if result.returncode != 0:
        raise NotifierSetupError(f"psql failed ({result.returncode}): {result.stderr.strip()}")

    return [line.split(FIELD_SEP) for line in result.stdout.splitlines() if line]


# --------------------------------------------------------------------------
# Reads. Three of them, and that is the whole surface.
# --------------------------------------------------------------------------


def published_since(cutoff: datetime) -> list[dict[str, str]]:
    """Signals published at or after `cutoff`, with the tier §10.1 caps on.

    The tier is joined live rather than read from the sealed payload because
    the payload does not carry one — and because a symbol demoted since
    publication should stop pushing, which is the direction that errs quietly.
    """
    rows = _psql(
        f"""
        select s.signal_id,
               s.symbol,
               s.timeframe,
               s.direction,
               s.archetype,
               s.grade,
               s.final_confidence,
               s.entry_proximal,
               s.entry_distal,
               s.invalidation_level,
               s.published_at,
               coalesce(y.tier, 'UNKNOWN'),
               coalesce((s.payload::jsonb -> 'risk' -> 'condition_tags')::text, '[]')
          from detection.signals s
          left join market.symbols y on y.exchange_symbol = s.symbol
         where s.published_at >= timestamptz '{cutoff.isoformat()}'
         order by s.published_at
        """
    )

    fields = (
        "signal_id",
        "symbol",
        "timeframe",
        "direction",
        "archetype",
        "grade",
        "confidence",
        "entry_proximal",
        "entry_distal",
        "invalidation",
        "published_at",
        "tier",
        "condition_tags",
    )

    return [dict(zip(fields, row)) for row in rows]


def terminal_transitions(signal_ids: list[str]) -> list[dict[str, str]]:
    """Terminal transitions of signals this notifier already alerted.

    Two filters earn their place:

    * `refresh = false`. §10.3's merge appends a *re-detection* to a live
      signal. It is evidence that the setup is still there, not a state
      change, and pushing it would announce something that did not happen.
    * terminal states only. A `stress_test` row is a candle whose wick went
      through the invalidation and whose close did not — §12.3 is explicit
      that this "does not fail the signal". Pushing it is crying wolf.
    """
    if not signal_ids:
        return []

    quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in signal_ids)
    states = ", ".join(f"'{s}'" for s in TERMINAL_STATES)

    rows = _psql(
        f"""
        select t.transition_id,
               t.signal_id,
               t.to_state,
               t.at_candle_open_time
          from detection.signal_transitions t
         where t.signal_id in ({quoted})
           and t.to_state in ({states})
           and t.refresh = false
         order by t.at_candle_open_time
        """
    )

    fields = ("transition_id", "signal_id", "to_state", "at")

    return [dict(zip(fields, row)) for row in rows]


def assert_tier_readable() -> None:
    """Fail closed if the tier cap cannot be evaluated.

    §10.1 caps push at tier 1-3 and the scanned set contains two INELIGIBLE
    symbols. A notifier that could not read tiers and carried on would push
    them. Better to refuse to run and say exactly which grant is missing.
    """
    try:
        rows = _psql("select count(*) from market.symbols")
    except NotifierSetupError as exc:
        raise NotifierSetupError(
            "cannot read symbol tiers, so §10.1's Tier 1-3 cap cannot be "
            "applied and LISTAUSDT/LITEBUSDT would push. Apply the grant in "
            "ops/db/notify-role.sql as the owner role:\n"
            "  GRANT USAGE ON SCHEMA market TO scanner_notify;\n"
            "  GRANT SELECT (exchange_symbol, tier, status)\n"
            "    ON market.symbols TO scanner_notify;\n"
            f"psql said: {exc}"
        ) from exc

    if not rows:
        raise NotifierSetupError("market.symbols returned nothing; refusing to run")


# --------------------------------------------------------------------------
# Ledger. Append-only, greppable, and the reason a restart does not re-send.
# --------------------------------------------------------------------------


def load_ledger() -> tuple[set[str], set[str]]:
    """Return (ids already decided, ids actually pushed).

    Two sets rather than one because "decided" and "alerted" are different
    questions and conflating them costs in both directions. A signal dropped
    for grade is decided — asking again next minute would write the same
    suppression line five times while the window holds it — but it was never
    alerted, so its outcome must not be pushed either. Only the ids in the
    second set have had an entry announced, and only those earn a
    close-the-loop message.
    """
    if not LEDGER.exists():
        return set(), set()

    decided: set[str] = set()
    pushed: set[str] = set()

    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")

        if len(parts) < 3:
            continue

        kind, key = parts[1], parts[2]
        decided.add(kind + ":" + key if kind == "transition" else key)

        if kind == "signal":
            pushed.add(key)

    return decided, pushed


def ledger_count_today(now: datetime) -> int:
    """How many messages actually reached Telegram today, for §10.1's cap.

    Only `signal` and `transition` rows count. The ledger also records
    suppressions, cold-start marks and give-ups, and counting those would let
    a quiet day of *rejections* exhaust a cap that exists to ration the
    recipient's attention — attention nothing spent.
    """
    if not LEDGER.exists():
        return 0

    today = now.date().isoformat()
    sent = 0

    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")

        if len(parts) >= 3 and parts[0].startswith(today) and parts[1] in {"signal", "transition"}:
            sent += 1

    return sent


def ledger_append(now: datetime, kind: str, key: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)

    with LEDGER.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{now.isoformat()}\t{kind}\t{key}\n")


def attempts_for(key: str) -> int:
    if not ATTEMPTS.exists():
        return 0

    for line in ATTEMPTS.read_text(encoding="utf-8").splitlines():
        name, _, count = line.partition("\t")

        if name == key:
            return int(count or 0)

    return 0


def bump_attempt(key: str) -> int:
    counts: dict[str, int] = {}

    if ATTEMPTS.exists():
        for line in ATTEMPTS.read_text(encoding="utf-8").splitlines():
            name, _, count = line.partition("\t")

            if name:
                counts[name] = int(count or 0)

    counts[key] = counts.get(key, 0) + 1

    ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    ATTEMPTS.write_text(
        "".join(f"{name}\t{count}\n" for name, count in sorted(counts.items())),
        encoding="utf-8",
        newline="\n",
    )

    return counts[key]


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LOG.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{stamp} {message}\n")


# --------------------------------------------------------------------------
# Delivery.
# --------------------------------------------------------------------------


def load_env() -> tuple[str, str]:
    values = dict(os.environ)

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()

            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            name, _, value = stripped.partition("=")
            values.setdefault(name.strip(), value.strip())

    token = values.get("SCANNER_NOTIFY_BOT_TOKEN", "")
    chat = values.get("SCANNER_NOTIFY_CHAT_ID", "")

    if not token or not chat:
        raise NotifierSetupError(
            f"SCANNER_NOTIFY_BOT_TOKEN and SCANNER_NOTIFY_CHAT_ID must be set "
            f"(environment or {ENV_FILE})"
        )

    return token, chat


def send(token: str, chat: str, text: str) -> None:
    """POST to Telegram. The token never reaches a log line, including on error."""
    payload = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")

    request = urllib.request.Request(  # noqa: S310 — fixed https host
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            if response.status != 200:
                raise NotifierSetupError(f"telegram returned {response.status}")
    except urllib.error.HTTPError as exc:
        raise NotifierSetupError(f"telegram HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise NotifierSetupError(f"telegram unreachable: {exc.reason}") from None


def format_signal(row: dict[str, str]) -> str:
    tags = ""

    if "wash_risk" in row["condition_tags"]:
        tags = "\n⚠ wash_risk"

    return (
        f"{row['symbol']} {row['timeframe']} {row['direction']}\n"
        f"{row['archetype']} · grade {row['grade']} · confidence {row['confidence']}\n"
        f"entry {row['entry_proximal']} → {row['entry_distal']}\n"
        f"invalidation {row['invalidation']} (close beyond, not touch)\n"
        f"published {row['published_at']}{tags}"
    )


def format_transition(row: dict[str, str]) -> str:
    return f"{row['signal_id'][:12]}… → {row['to_state']}\nat {row['at']}"


# --------------------------------------------------------------------------


def main() -> int:
    dry_run = os.environ.get("SEND") == "0"
    now = datetime.now(timezone.utc)

    assert_tier_readable()

    token, chat = ("", "") if dry_run else load_env()

    fresh = published_since(now - PUBLISH_WINDOW)
    decided, pushed = load_ledger()

    # Cold start. Without this, a clock or timezone mistake on the first run
    # would push every signal the window happens to catch — and the nine rows
    # already in the table are from retired algorithm versions.
    #
    # The marker line is written first and unconditionally, because the ledger's
    # existence is what ends the cold start. Writing only the per-signal rows
    # leaves no file at all on a quiet first run -- which is the normal case at
    # one signal a fortnight -- so every later run cold-starts again, and the
    # first real signal to arrive is marked seen instead of sent. The tool
    # would swallow exactly the message it exists to deliver, and the log would
    # say it had done the right thing.
    if not LEDGER.exists():
        ledger_append(now, "coldstart", "-")

        for row in fresh:
            ledger_append(now, "coldstart", row["signal_id"])

        log(f"cold start: {len(fresh)} signal(s) marked seen, none sent")
        return 0

    pending: list[tuple[str, str, str]] = []

    for row in fresh:
        if row["signal_id"] in decided:
            continue

        if row["grade"] not in PUSH_GRADES:
            log(f"suppressed grade={row['grade']} {row['symbol']} {row['signal_id'][:12]} (§10.1)")
            ledger_append(now, "suppressed-grade", row["signal_id"])
            continue

        if row["tier"] not in PUSH_TIERS:
            log(f"suppressed tier={row['tier']} {row['symbol']} {row['signal_id'][:12]} (§10.1)")
            ledger_append(now, "suppressed-tier", row["signal_id"])
            continue

        pending.append(("signal", row["signal_id"], format_signal(row)))

    for row in terminal_transitions(sorted(pushed)):
        if "transition:" + row["transition_id"] in decided:
            continue

        pending.append(("transition", row["transition_id"], format_transition(row)))

    sent_today = ledger_count_today(now)

    for kind, key, text in pending:
        # §10.1: when the cap binds the lowest-priority pending alerts drop
        # first. Closing the loop on a signal already pushed outranks a new
        # one — a trader who was told about an entry and never told it died is
        # worse off than one who missed the entry.
        if kind == "signal" and sent_today >= DAILY_CAP:
            log(f"suppressed cap={DAILY_CAP} reached, dropped signal {key[:12]} (§10.1)")
            ledger_append(now, "suppressed-cap", key)
            continue

        if dry_run:
            log(f"DRY RUN would send {kind} {key[:12]}")
            continue

        try:
            send(token, chat, text)
        except NotifierSetupError as exc:
            tries = bump_attempt(key)

            if tries >= MAX_ATTEMPTS:
                log(f"FAILED {kind} {key[:12]} after {tries} attempts: {exc}")
                ledger_append(now, "failed", key)
            else:
                log(f"retry {tries}/{MAX_ATTEMPTS} {kind} {key[:12]}: {exc}")

            continue

        ledger_append(now, kind, key)
        sent_today += 1
        log(f"sent {kind} {key[:12]}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (NotifierContractError, NotifierSetupError) as error:
        log(f"ERROR {error}")
        print(f"notify_signals: {error}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
