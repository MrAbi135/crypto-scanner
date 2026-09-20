# Interim signal notifier

**Temporary. Deleted at Sprint S18** (DEVELOPMENT_ROADMAP.md v2.1.1, S18's first
bullet). `backend/tests/unit/test_interim_notifier_is_retired_at_s18.py` fails
the build if this directory outlives the alert engine.

Pushes newly published signals to Telegram so the developer does not poll the
API by hand at a rate of roughly one signal a fortnight. It is **not** an alert
engine: SLS §10's priority split, storm breaker, cooldowns, quiet hours and
delivery ledger are S18's job and are absent here. What it does implement is
listed in the module docstring, with the rule each part cites.

## What it reads

The database, directly, as `scanner_notify` — a role that can `SELECT`
`detection.signals`, `detection.signal_transitions` and three columns of
`market.symbols`, and nothing else (`ops/db/notify-role.sql`). Notably it
cannot read `detection.setups`, the table behind `GET /api/v1/rankings`, which
holds below-floor candidates that never faced §15.3. That refusal is the
contract; the path whitelist inside the script is only the second layer.

## Install

1. **Create the role** (owner, once, after migrations):

   ```sql
   CREATE ROLE scanner_notify
       LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
   ```

   ```bash
   docker exec -i scanner-dev-db-1 psql -U scanner -d scanner -v ON_ERROR_STOP=1 \
     < ops/db/notify-role.sql
   ```

2. **Create the bot and the secrets file.** Talk to `@BotFather` in Telegram,
   create a bot, and send it one message so it has a chat to reply to. Then, on
   the VM:

   ```bash
   install -m 600 /dev/null ops/env/notify.local.env
   ```

   and put two lines in it:

   ```
   SCANNER_NOTIFY_BOT_TOKEN=<from BotFather>
   SCANNER_NOTIFY_CHAT_ID=<your chat id>
   ```

   `ops/env/*.local.env` is gitignored. The token belongs on the VM and nowhere
   else — not in the repository, not in a commit message, not in a log line.

3. **Dry run first.** `SEND=0` reads everything and sends nothing:

   ```bash
   cd ~/crypto-scanner && SEND=0 python3 ops/notify/notify_signals.py
   cat ~/soak-logs/notify.log
   ```

   The first real run is a cold start: it marks everything currently visible as
   seen and sends nothing, so a clock or timezone mistake cannot replay old
   signals. Expect `cold start: N signal(s) marked seen, none sent`.

4. **Install the cron entry**, alongside the hourly invariants check:

   ```
   * * * * * cd $HOME/crypto-scanner && python3 ops/notify/notify_signals.py >> $HOME/soak-logs/notify.cron.log 2>&1
   ```

## Disable

Comment out the crontab line. Nothing else depends on it — the notifier runs
outside every scanner container, reads only, and cannot be seen by the engine.
If it dies, detection does not notice.

## Files it writes

| Path | What |
|---|---|
| `~/soak-logs/notified.txt` | append-only ledger: what was sent, suppressed, or given up on |
| `~/soak-logs/notify_attempts.tsv` | delivery retry counters |
| `~/soak-logs/notify.log` | every send **and every suppression, with its reason** |

§10.1 requires "honest suppression, never silent", which is why the third file
records the drops as loudly as the sends.

## Expect it to be quiet

Nine signals exist in the project's history and the most recent was published
2026-09-07. At the current rate this fires roughly once every ten days. A log
full of nothing is the expected output, not a fault — `notify.log` staying
silent while `~/soak-logs/invariants.log` keeps passing is the system working.
