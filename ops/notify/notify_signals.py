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

* Only signals the ledger has not already decided, scanned over a bounded
  `LOOKBACK`. Freshness at dispatch is a TTL test, not a window test -- see
  `LOOKBACK` for the measurement showing why a window could never be one.
* Only grades S, A and B -- every grade a published signal can carry. §10.1
  pushed S and A only until v1.0.17 (owner, 2026-10-19), which pushes B too.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path

# --------------------------------------------------------------------------
# Doctrine constants. Each one cites the rule it implements; a number without
# a citation here is a number somebody invented.
# --------------------------------------------------------------------------

# §15.3(2) checks freshness at the *publication* moment; §10.2 wants it at the
# *dispatch* moment. Those are different instants and nothing in the row
# reconciles them. This file used to reconcile them with a five-minute window
# on `published_at`, and that window could never be satisfied.
#
# `published_at` is the candle's **OPEN** time. `confluence_replay.py:617`
# passes `series[-1].open_time` as `event_at`, line 1330 writes
# `published_at=event_at`, and line 1353 names the same value
# `at_candle_open_time`. The comment that stood here asserted it was the CLOSE
# time and sized 300s against a measured ~69s write lag. Both halves were
# honest; the premise joining them was wrong, so the number came out
# unreachable -- a signal cannot exist until its candle has closed, so the
# moment it first exists its `published_at` is already one whole period old.
#
# Measured, 2026-10-05, not inferred. SUIUSDT M5: `published_at` 04:20:00Z, and
# the pass carrying `open_time 04:20:00` completed at 04:25:17.1Z -- an age at
# first sight of **317s** against a 300s window. Seventeen seconds too old, so
# the row was never once visible to this file, which logged nothing all day
# about a signal that sat in the table the whole time. On M15/H1/H4 the
# shortfall is 15, 60 and 240 minutes, so no timeframe could ever have pushed.
# The push path has therefore never run in production: the last grade S or A
# signal was 2026-09-07, which is why four weeks of silence read as a quiet
# market rather than as a broken notifier.
#
# The window is now only a **scan bound**: wide enough that nothing can fall
# outside it (24h clears H4's 4h period six times over and survives a cron
# outage of nearly a day), narrow enough to keep the query and the cold start
# bounded. What has already been handled is the ledger's question -- which is
# the question that was always really being asked -- and whether a signal is
# still worth sending is `dispatch_lifetime_seconds` below.
LOOKBACK = timedelta(hours=24)

# Seconds per candle on every timeframe the engine can emit (SLS §0.2; M1 is
# deliberately absent there, so it is absent here). A timeframe missing from
# this map is a code defect rather than something to guess at, and
# `dispatch_lifetime_seconds` raises instead of inventing a lifetime for it.
TIMEFRAME_SECONDS = {
    "M5": 300,
    "M15": 900,
    "H1": 3_600,
    "H4": 14_400,
    "D1": 86_400,
    "W1": 604_800,
}

# §10.2 wants freshness at dispatch, and a 24-hour scan bound would otherwise
# let a cron outage push signals whose setups expired hours ago. A signal's own
# `ttl_candles` is the doctrine's measure of how long it stays relevant (§12),
# so dispatch uses exactly that and invents nothing. The fallback exists only
# so that a null or zero cannot silently mean "immortal"; two candles is the
# shortest span that cannot suppress a signal on first sight, which is already
# about one period old by the paragraph above.
TTL_FALLBACK_CANDLES = 2

# §10.1 (v1.0.17): High is grade S, Medium is grade A, Low is grade B, and all
# three push. Until v1.0.17 Low read "No push; dashboard feed + optional
# digest"; the owner ruled on 2026-10-19 that B reaches Telegram as well. A
# published signal has no other grade, so this rejects only a malformed row.
PUSH_GRADES = frozenset({"S", "A", "B"})

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
    # SLS v1.0.15: a laddered signal that reached TP1 and was stopped at the
    # entry. Missing here, a pushed M5 signal that closed flat would never get
    # its close-the-loop message -- the trader told about an entry and never
    # told it ended. A test holds this tuple to the engine's own terminal set.
    "CLOSED_FLAT",
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
               coalesce((s.payload::jsonb -> 'risk' -> 'condition_tags')::text, '[]'),
               s.ttl_candles,
               greatest(0, round(extract(epoch from now() - s.published_at)))::bigint,
               s.target_bands,
               coalesce(s.payload::jsonb -> 'invalidation' ->> 'rule', '')
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
        "ttl_candles",
        "age_seconds",
        "target_bands",
        "invalidation_rule",
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
               t.at_candle_open_time,
               t.trigger_evidence,
               s.symbol,
               s.timeframe,
               s.direction
          from detection.signal_transitions t
          join detection.signals s on s.signal_id = t.signal_id
         where t.signal_id in ({quoted})
           and t.to_state in ({states})
           and t.refresh = false
         order by t.at_candle_open_time
        """
    )

    fields = (
        "transition_id",
        "signal_id",
        "to_state",
        "at",
        "trigger_evidence",
        "symbol",
        "timeframe",
        "direction",
    )

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
# Dispatch freshness. `LOOKBACK` above is a scan bound and answers nothing
# about relevance; this does.
# --------------------------------------------------------------------------


def dispatch_lifetime_seconds(row: dict[str, str]) -> int:
    """How long after its candle opened a signal is still worth sending.

    `ttl_candles` x the timeframe's period, which is §12's own measure and not
    a number invented here.

    It raises rather than guessing when the timeframe is not one the engine
    emits. A new timeframe string reaching this file means the scanned set and
    `TIMEFRAME_SECONDS` have diverged, and a guess in either direction is
    silently wrong forever: too short suppresses every signal on that
    timeframe, too long pushes expired ones. The cron log is where that belongs.
    """
    period = TIMEFRAME_SECONDS.get(row["timeframe"])

    if period is None:
        raise NotifierContractError(
            f"timeframe {row['timeframe']!r} is not in TIMEFRAME_SECONDS; "
            "refusing to guess a dispatch lifetime"
        )

    try:
        candles = int(row["ttl_candles"])
    except (TypeError, ValueError):
        candles = 0

    return (candles if candles > 0 else TTL_FALLBACK_CANDLES) * period


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


# SLS v1.0.15 §15.2: the rule name a risk stop is sealed under. Such a signal's
# R is measured from the entry's PROXIMAL edge; every other rule measures it
# from the mid (`SignalLevels.anchor` in the domain).
RISK_STOP_RULE = "risk_stop"

# The owner's ruling named three -- "TP1 2R, TP2 3R, TP3 4R" -- and no cap
# after them; the API prices the same three (`interfaces/api/targets.py`).
RUNGS_SHOWN = 3


def _json_object(text: str | None) -> dict:
    """A sealed JSON column as a dict, or {} if it is absent or not an object."""
    try:
        value = json.loads(text or "")
    except ValueError:
        return {}

    return value if isinstance(value, dict) else {}


def ladder_of(row: dict[str, str]) -> dict | None:
    """The signal's sealed TP ladder, or None for a pool-exit signal."""
    ladder = _json_object(row.get("target_bands")).get("ladder")

    return ladder if isinstance(ladder, dict) and ladder else None


def rung_prices(row: dict[str, str], ladder: dict) -> list[tuple[int, Decimal, Decimal]]:
    """(n, R-multiple, price) for the first rungs, from the sealed row.

    This file is standard-library only and cannot import the domain, so the
    arithmetic is restated here -- the one copy outside the domain, held to
    `SignalLevels.rung_price` on longs, shorts and both anchors by
    `test_the_notifier_prices_the_rungs_as_the_domain_does`.
    """
    proximal = Decimal(row["entry_proximal"])
    distal = Decimal(row["entry_distal"])
    invalidation = Decimal(row["invalidation"])

    if row.get("invalidation_rule") == RISK_STOP_RULE:
        anchor = proximal
    else:
        anchor = (proximal + distal) / 2

    unit = abs(anchor - invalidation)
    sign = 1 if row["direction"] == "UP" else -1
    start = Decimal(str(ladder["start_r"]))
    step = Decimal(str(ladder["step_r"]))

    rungs = []

    for n in range(1, RUNGS_SHOWN + 1):
        r = start + step * (n - 1)
        rungs.append((n, r, anchor + sign * r * unit))

    return rungs


def _plain(value: Decimal) -> str:
    """A number without numeric(38,18)'s trailing zeros and never as an exponent."""
    return format(value.normalize(), "f")


def _shown(text: str) -> str:
    """A sealed numeric column as a trader reads it: 81, not 81.000000000000000000.

    psql hands numeric(38,18) back with all eighteen places. Anything that does
    not parse is shown as it came -- a malformed column must cost the tidiness,
    never the alert.
    """
    try:
        return _plain(Decimal(text))
    except (InvalidOperation, TypeError, ValueError):
        return text


# SLS §8.6's names, so a trader reads the setup rather than a code.
ARCHETYPE_NAMES = {
    "A1": "Sweep Reversal",
    "A2": "Breaker Retest",
    "A3": "Continuation Pullback",
    "A4": "FVG Continuation",
    "A5": "Range Liquidity Play",
}
TF_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
PKT = timedelta(hours=5)
RULE = "━━━━━━━━━━━━━━━━━━"

OUTCOME_HEADLINES = {
    "SUCCESS": "✅ TARGET HIT",
    "FAILED": "❌ STOPPED OUT",
    "EXPIRED_ACTIVE": "⌛ EXPIRED IN TRADE",
    "EXPIRED_UNTOUCHED": "⌛ EXPIRED, ENTRY NEVER REACHED",
    "INVALIDATED_EARLY": "🚫 CANCELLED BEFORE ENTRY",
    "CLOSED_FLAT": "➖ CLOSED FLAT, STOP AT ENTRY",
}


def _side(direction: str) -> str:
    if direction == "UP":
        return "🟢 LONG"
    if direction == "DOWN":
        return "🔴 SHORT"
    return direction


def _parse_time(text: str) -> datetime | None:
    """psql's timestamptz text ('2026-10-17 13:15:00+00') as an aware datetime."""
    value = (text or "").strip().replace(" ", "T", 1)

    if value.endswith("+00"):
        value += ":00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc)
    return f"{utc:%d %b %H:%M} UTC · {utc + PKT:%H:%M} PKT"


def _signal_time(row: dict[str, str]) -> str:
    """When the signal became actionable: its candle's CLOSE, not `published_at`.

    `published_at` is the candle's OPEN time (see `dispatch_lifetime_seconds`);
    the setup was decided when that candle closed one period later.
    """
    opened = _parse_time(row.get("published_at", ""))
    minutes = TF_MINUTES.get(row.get("timeframe", ""))

    if opened is None or minutes is None:
        return row.get("published_at", "")

    return _clock(opened + timedelta(minutes=minutes))


def _valid_for(row: dict[str, str]) -> str:
    try:
        candles = int(row["ttl_candles"])
    except (KeyError, ValueError):
        return ""

    minutes = TF_MINUTES.get(row.get("timeframe", ""))

    if minutes is None:
        return f"{candles} candles"

    hours = candles * minutes / 60
    span = f"{hours:g} h" if hours < 48 else f"{hours / 24:g} days"

    return f"{candles} candles (~{span})"


def _pool_target(row: dict[str, str]) -> tuple[Decimal, Decimal] | None:
    primary = _json_object(row.get("target_bands")).get("primary")

    if not isinstance(primary, dict):
        return None

    try:
        return Decimal(str(primary["low"])), Decimal(str(primary["high"]))
    except (KeyError, InvalidOperation):
        return None


def _risk_reward(row: dict[str, str], target: tuple[Decimal, Decimal]) -> str | None:
    """Reward : risk as §12.4 measures it -- R from the entry mid, target on touch."""
    try:
        proximal = Decimal(row["entry_proximal"])
        distal = Decimal(row["entry_distal"])
        stop = Decimal(row["invalidation"])
    except (KeyError, InvalidOperation):
        return None

    mid = (proximal + distal) / 2
    risk = abs(mid - stop)
    up = row.get("direction") == "UP"
    near = target[0] if up else target[1]
    reward = (near - mid) if up else (mid - near)

    if risk <= 0 or reward <= 0:
        return None

    return f"1 : {reward / risk:.1f}"


def _sort_key(text: str) -> tuple[int, Decimal]:
    try:
        return (0, Decimal(text))
    except (InvalidOperation, TypeError, ValueError):
        return (1, Decimal(0))


def format_signal(row: dict[str, str]) -> str:
    """The alert a trader reads: side, levels, risk, setup, time.

    Plain text, no parse mode: a formatting error in Telegram's markup rejects
    the whole message, and an alert lost to styling is the one failure this
    file exists to prevent.
    """
    direction = row.get("direction", "")
    lower, upper = sorted(
        (_shown(row["entry_proximal"]), _shown(row["entry_distal"])), key=_sort_key
    )
    closes = "candle close below" if direction == "UP" else "candle close above"
    lines = [f"{_side(direction)} · {row['symbol']} · {row['timeframe']}", RULE]

    ladder = ladder_of(row)

    if ladder is None:
        lines.append(f"📍 Entry zone: {lower} – {upper}")
        lines.append(f"🛑 Stop: {_shown(row['invalidation'])} ({closes})")
        target = _pool_target(row)

        if target is not None:
            low, high = (_plain(v) for v in target)
            lines.append(f"🎯 Target: {low if low == high else f'{low} – {high}'}")
            ratio = _risk_reward(row, target)

            if ratio:
                lines.append(f"⚖️ Risk : Reward = {ratio}")
    else:
        # SLS v1.0.15 §12.3: on a laddered signal the entry is the zone's
        # proximal edge, the stop is a TOUCH, and it trails behind the TPs.
        lines.append(f"📍 Entry: {_shown(row['entry_proximal'])} (zone {lower} – {upper})")
        lines.append(f"🛑 Stop: {_shown(row['invalidation'])} (on touch)")

        try:
            rungs = rung_prices(row, ladder)
        except (KeyError, InvalidOperation):
            rungs = []

        if rungs:
            for n, r, price in rungs:
                lines.append(f"🎯 TP{n}: {_plain(price)} ({_plain(r)}R)")
            lines.append("🔁 At TP1 the stop moves to entry, then trails one TP behind · no cap")
        else:
            lines.append("🎯 TP prices unavailable -- see the dashboard")

    lines.append(RULE)
    code = row.get("archetype", "")
    name = ARCHETYPE_NAMES.get(code, "")
    lines.append(f"📐 Setup: {code}{f' · {name}' if name else ''}")
    lines.append(f"⭐ Grade {row['grade']} · Confidence {_shown(row['confidence'])}/100")

    valid = _valid_for(row)

    if valid:
        lines.append(f"⏳ Valid for: {valid}")

    lines.append(f"🕒 {_signal_time(row)}")

    if "wash_risk" in row.get("condition_tags", ""):
        lines.append("⚠️ Wash-trading risk flagged on this pair")

    lines.append("ℹ️ Not financial advice")

    return "\n".join(lines)


def format_transition(row: dict[str, str]) -> str:
    """The close-the-loop message: what happened to a signal already alerted."""
    state = row["to_state"]
    headline = OUTCOME_HEADLINES.get(state, state)
    parts = [row.get("symbol", ""), row.get("timeframe", "")]

    if row.get("direction"):
        parts.append(_side(row["direction"]))

    who = " · ".join(part for part in parts if part)
    lines = [f"{headline}{f' · {who}' if who else ''}", RULE]

    # SLS v1.0.15: a laddered signal books what its trailing stop locked in,
    # so SUCCESS alone does not say whether that was 2R or 6R.
    realised = _json_object(row.get("trigger_evidence")).get("realised_r")

    if realised not in (None, ""):
        try:
            # Two places: realised_r is a ratio of prices and arrives with up
            # to 28 digits (2026-10-21: "+0.01449065352847413418345167367R"
            # reached the phone).
            booked = Decimal(str(realised)).quantize(Decimal("0.01"))
            # EXPIRED_ACTIVE's R is the open R when the TTL ran out, not one
            # a stop or target booked, so it must not read as a result.
            label = "Open at expiry" if state == "EXPIRED_ACTIVE" else "Result"
            lines.append(f"💰 {label}: {'+' if booked > 0 else ''}{_plain(booked)}R")
        except InvalidOperation:
            pass

    moment = _parse_time(row.get("at", ""))
    lines.append(f"🕒 {_clock(moment) if moment else row.get('at', '')}")
    lines.append(f"🔖 Signal {row['signal_id'][:12]}…")

    return "\n".join(lines)


# --------------------------------------------------------------------------


def main() -> int:
    dry_run = os.environ.get("SEND") == "0"
    now = datetime.now(timezone.utc)

    assert_tier_readable()

    token, chat = ("", "") if dry_run else load_env()

    fresh = published_since(now - LOOKBACK)
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

        # §10.2's freshness, now that `LOOKBACK` is only a scan bound. A signal
        # first seen on time is ~1 period old and nowhere near its TTL; this
        # only bites after an outage, which is exactly when a burst of expired
        # pushes would otherwise arrive.
        age = int(row["age_seconds"] or 0)
        lifetime = dispatch_lifetime_seconds(row)

        if age > lifetime:
            log(
                f"suppressed stale age={age}s > ttl={lifetime}s "
                f"{row['symbol']} {row['timeframe']} {row['signal_id'][:12]} (§10.2)"
            )
            ledger_append(now, "suppressed-stale", row["signal_id"])
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
