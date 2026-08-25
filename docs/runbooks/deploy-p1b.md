# Runbook — Deploy P1b to the staging VM

## Purpose

Move the staging host from `008_outbox_events` to `018_append_only_guards`
and onto the current build, without losing candles and without filling the
disk.

This is not a routine deploy. **Ten migrations run at once**, one of them
rebuilds a 17 GB table, and the host has 22 GB free.

*Numbers re-measured 2026-08-24 before the deploy. The first draft of this
runbook said 15 GB and 23 GB; the table grew while the soak ran, which is the
reason step 1 exists and the reason it says to write the numbers down rather
than trust the ones printed here.*

## Before you start — read this

**A 72 h soak of the old build does not certify the new one.** The soak that
ends `2026-08-24T08:29:47Z` ran `release: soak`, which predates every fix made
on 23–24 August. It proves the *operational* property G1b asks for — no
crashes, no restarts, no backlog — of **that** build.

G1b's first criterion is "engine runs unattended ≥ 72 h". Deploying replaces
the binary that earned it. The choices are:

* certify G1b against the soak build and accept that the certified build is
  not the one running afterwards; or
* deploy, then run a fresh 72 h soak and certify that.

The second is the honest one. **This is the developer's call and it should be
made before step 1, not discovered at step 9.**

Two of G1b's four criteria are not covered by any soak in any case: the chart
rendering live objects for an unselected symbol (S13a), and `kill -9` losing
no closes. Neither is in this runbook.

## Preconditions

| | |
|---|---|
| Host | `ubuntu@141.148.205.213` |
| Key | `ssh -i <key path>` — the key file is never opened or copied |
| Compose | `/home/ubuntu/crypto-scanner/ops/compose/docker-compose.dev.yml` |
| Current revision | `008_outbox_events` |
| Pending | `009_trade_aggregates`, `010_wash_risk`, `011_interaction_identity`, `012_setups`, `013_param_set`, `014_signals`, `015_signal_transitions`, `016_signal_outcomes`, `017_transition_refresh`, `018_append_only_guards` |
| Free disk needed | **≥ 20 GB** before step 4 |
| Cost of the ten | 011 is the whole cost. 014–018 create empty tables and triggers and are effectively free. |

Set these once per session:

```bash
ssh -i "<key>" ubuntu@141.148.205.213
cd ~/crypto-scanner
export DC="docker compose -f ops/compose/docker-compose.dev.yml"
export PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner"
```

## Steps

### 1. Record the before-state

```bash
$PSQL -tAc "select version_num from alembic_version"
$PSQL -tAc "select count(*) from detection.ict_zone_interactions"
$PSQL -tAc "select pg_size_pretty(pg_database_size('scanner'))"
df -h / | tail -1
```

Expected: `008_outbox_events`, ~27M rows and rising, ~22 GB free.

Measured 2026-08-24T10:0x Z: `008_outbox_events`, **27,013,345** rows, table
17 GB, database 18 GB, **22 GB free**.

**Write these down.** Step 9 compares against them, and "about the same" is
not a comparison.

### 2. Snapshot the volume

Take an Oracle Cloud **block-volume backup** of the boot volume from the
console, and wait for it to report `AVAILABLE`.

This is the only real rollback. A `pg_dump` of a 15 GB database on this host
takes long enough to be its own outage, and migration 011 is the one step
whose failure mode is "no disk left", which a dump cannot help with.

> The console is the developer's to drive — an agent cannot and should not
> take it. Do not proceed until the backup shows `AVAILABLE`.

### 3. Stop the writers, leave ingest running

```bash
$DC stop engine worker
$DC ps
```

Expected: `engine` and `worker` exited; `db`, `redis`, `ingest`, `api` up.

Ingest keeps running on purpose. It publishes closes to
`scanner:stream:candle-closed`, the engine consumes them through the `engine`
consumer group, and unacked messages persist — so the closes that happen
during the migration are waiting when the engine returns. Stopping ingest is
what would lose them.

Migration 011 drops and recreates `detection.ict_zone_interactions`. The
engine writes to that table on every pass; it must not be running.

### 4. Check disk headroom, and stop if it is short

```bash
df -h / | tail -1
```

**Do not run step 5 with less than 20 GB free.** During the rebuild the old
table (15 GB) and the new one coexist until commit, plus sort temp.

If it is short, reclaim first:

```bash
docker system prune -f
$PSQL -c "VACUUM (ANALYZE) detection.ict_zone_interactions"
df -h / | tail -1
```

### 5. Pull, rebuild `api`, then apply the migrations

**Order matters, and the first draft of this runbook had it wrong.** Alembic
runs *inside the api image*, and `docker compose run` does not rebuild — so an
unrebuilt image migrates only as far as the revisions it happens to contain,
and reports success. On 2026-08-24 the host was still on the `#62` build,
eleven revisions behind the repo.

```bash
git fetch origin && git checkout main && git reset --hard origin/main
$DC build api
```

Confirm the versions directory holds what you expect before continuing:

```bash
ls backend/src/scanner/infrastructure/persistence/alembic/versions/ | tail -8
```

Then, **inside `tmux` or `screen`**:

```bash
time $DC run --rm api alembic upgrade head
```

Not optional. The rebuild runs long enough that an ssh drop is likely, and on
2026-08-24 one happened. The container survived and the migration completed —
`docker compose run` without `-d` leaves the container running when its client
dies — but you lose the output, the timing, and the exit code, and the
temptation is then to start a second one. Do not: check
`docker ps | grep api-run-` and `pg_stat_activity` first.

**No `-e SCANNER_DB_DSN`.** The earlier draft passed one with a literal
`scanner:scanner` password. The real credential is in `ops/env/dev.env`, which
compose already loads via `env_file`; the override fails with
`InvalidPasswordError` in about three seconds. Harmless — nothing runs — but it
reads as a database fault and is not one.

**Do not pipe the command through `tail`.** The pipeline's exit status is
`tail`'s, so a failed migration reports success. Redirect to a file instead.

Alembic wraps the whole upgrade in one transaction, so **009 through 018 are
atomic together**: any failure rolls all ten back and leaves the database on
`008` with the original table intact. There is no half-migrated state to
repair.

**Measured 2026-08-24: 62 minutes.** 011 dedups 27 M rows on two ARM OCPUs.
Budget an hour and a half; do not interrupt it, because a killed migration
rolls back and the time is spent for nothing.

Watch `df` while it runs, and know what you are looking at. The `DISTINCT ON`
sorts the whole table, and the sort spills to
`$PGDATA/base/pgsql_tmp` — **12 GB of it here, on a 17 GB table**. That space
looks exactly like the new table filling up and is not: it comes back when the
sort finishes. Tell them apart directly rather than guessing from `df`:

```bash
docker exec -i scanner-dev-db-1 du -sh /var/lib/postgresql/data/base/pgsql_tmp
docker exec -i scanner-dev-db-1 du -sh /var/lib/postgresql/data/pg_wal
```

WAL is bounded by `max_wal_size` (1 GB here) and is never the problem. When the
wait event turns to `IO=BufFileRead` the spill phase is over and the disk stops
falling — that is the moment the risk passes.

**The failure modes are not equal.** A migration that runs out of temp space
aborts, rolls back, returns the space, and leaves the database on `008` — the
runbook's designed outcome. A *root filesystem* that fills takes the database
and ingest down with it. If free space approaches ~2.5 GB, terminate the
backend yourself rather than letting the filesystem decide:

```bash
$PSQL -tAc "select pg_terminate_backend(pid) from pg_stat_activity
            where query like '%ict_zone_interactions_rebuilt%' and pid <> pg_backend_pid()"
```

### 6. Confirm the migration did what it claims

```bash
$PSQL -tAc "select version_num from alembic_version"
$PSQL -tAc "select count(*) from detection.ict_zone_interactions"
$PSQL -tAc "select count(*) from (select 1 from detection.ict_zone_interactions
            group by zone_id, kind, observed_at having count(*) > 1) d"
$PSQL -tAc "select pg_size_pretty(pg_database_size('scanner'))"
df -h / | tail -1
```

Expected:

* revision `018_append_only_guards`;
* row count down by roughly **20×** — measured 2026-08-24: 27,013,345 → **942,281**, a 28.7× reduction;
* duplicate triples: **0**;
* database materially smaller, free disk materially larger — measured: 18 GB → **2.1 GB**, and 22 GB free → **38 GB**.

A row count that barely moved means the rebuild kept everything, and the
duplicate query is what proves it either way. Do not accept the count alone.

### 7. Rebuild the remaining images and start

`api` was rebuilt in step 5; the other three still carry the old build.

```bash
$DC build engine worker ingest
$DC up -d
$DC ps
```

Expected: six containers, all `healthy` within a minute or two.

### 8. Confirm the parameter set registered

```bash
$PSQL -tAF '|' -c "select engine, version, param_set_version, left(checksum,12), deployed_at
                   from detection.algo_versions order by deployed_at desc limit 5"
```

The engine also verifies the append-only guards at boot now (migration 018).
`ImmutabilityGuardsMissingError` at start-up means the triggers are missing or
disabled on this database — that is the check working. Do not bypass it; see
the least-privilege section below.

Expected: at least one row with a non-null checksum and today's timestamp.

If the engine container **exits at boot** with
`ParameterSetMismatchError`, that is the check working, not a deploy failure:
a parameter changed without `param_set_version` being incremented. Do not
restart it in a loop. Read the two digests in the message, decide which side
is right, and fix that — the version bump is a deliberate act with golden
re-validation attached (SLS Appendix A).

### 9. Confirm the engine is actually working

```bash
docker logs --since 10m scanner-dev-engine-1 2>&1 | grep -c detection_pass_completed
docker logs --since 10m scanner-dev-engine-1 2>&1 | grep -ciE "traceback|CRITICAL|unhandled"
$DC exec -T redis redis-cli XINFO GROUPS scanner:stream:candle-closed | paste - - | grep -E "pending|lag"
$PSQL -tAc "select count(*) from detection.setups"
```

Expected: passes climbing, **0** fault lines, `pending` and `lag` returning to
near zero as the engine drains the backlog step 3 allowed to build, and
`detection.setups` beginning to fill — that table is new in 012 and nothing
wrote to it before this deploy.

A `pending` count that stays high after ten minutes means the engine is not
keeping up; see `debugging.md`.

## Reading the output

| Signal | Healthy | What it means otherwise |
|---|---|---|
| `alembic_version` | `018_append_only_guards` | Migration rolled back; database is still on `008` and safe. Read the alembic error before retrying. |
| interaction rows | ~1.2 M | Unchanged ⇒ the rebuild did not run. Check the duplicate query before concluding anything. |
| duplicate triples | `0` | Non-zero ⇒ the unique index is missing; stop and investigate before starting the engine. |
| container restarts | `0` | Any restart ⇒ read that container's logs before restarting anything. |
| `ParameterSetMismatchError` | absent | Present ⇒ working as designed. Fix the parameter set, do not bypass. |
| `ImmutabilityGuardsMissingError` | absent | Present ⇒ the append-only triggers are missing or disabled on this database. Do **not** bypass; see the section below. |
| `immutability_grant_layer_absent` | present, for now | Expected until the least-privilege role below exists. Its absence would mean the engine is no longer the table owner — good, and worth confirming deliberately. |
| stream `pending` | falls to ~0 | Stays high ⇒ engine behind; do not stop it, watch first. |
| `scanner_detection_pass_seconds` | present, p95 ≤ 2 s | Absent ⇒ the engine is not emitting; the metric is new in this build, so on a rollback it disappears legitimately. Present and slow ⇒ read `DetectionPassSlow` before touching anything. |
| `scanner_process_info` | one series per process | Absent ⇒ `bootstrap` did not run on that process. |

## Escalation

**Migration fails.** Nothing to undo — the transaction rolled back. Capture the
error, `df -h`, and the row count, and stop. Do not re-run until the cause is
understood; a disk-space failure will simply happen again.

**Migration succeeds, engine will not start.** The database is ahead of the old
build and rolling the image back is *not* safe: `011` has already re-keyed the
interaction table and the old code writes the old id shape. Fix forward, or
restore the step 2 backup.

**Disk fills during step 5.** The transaction aborts and the space is returned
on rollback. Verify with `df -h`, then reclaim before retrying.

**Anything ambiguous.** Stop and write down what you saw. This host holds the
only 72 h of real detection data the project has; an hour spent reading is
cheaper than restoring it.


## Least-privilege role — the layer the code cannot install

DDD asks for three layers on `detection.signals`, `signal_transitions`, and
`signal_outcomes`: *"(a) no UPDATE grants to the application role on these
tables, (b) trigger-guard rejecting UPDATE/DELETE (defense in depth), (c) hash
chains/payload hashes for tamper evidence"*.

**(b) and (c) are in force.** Migration 018 installs the triggers and the
engine verifies them against the live catalog at every boot — it refuses to
start if they are missing or disabled.

**(a) is not, and cannot be closed from inside the application.** The engine
connects as `scanner`, which owns those tables. An owner's privileges cannot be
meaningfully revoked from itself, and a superuser ignores grants entirely. So
every boot logs `immutability_grant_layer_absent`, and that warning is honest:
two of the three layers are doing work.

Closing it needs a second role and a second secret, which is a deployment
decision rather than a code change. The SQL, for whoever makes it:

```sql
-- As the owner (`scanner`), once per database.
CREATE ROLE scanner_app LOGIN PASSWORD :'app_password';

GRANT USAGE ON SCHEMA detection, market, ops TO scanner_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA detection, market, ops TO scanner_app;

-- The point of the exercise: the crown jewels are insert-and-read only.
REVOKE UPDATE, DELETE, TRUNCATE ON
  detection.signals,
  detection.signal_transitions,
  detection.signal_outcomes
FROM scanner_app;

-- Tables added later inherit the broad grant, so re-run the REVOKE above
-- whenever a new immutable table appears. Default privileges cannot express
-- "everything except these three".
ALTER DEFAULT PRIVILEGES IN SCHEMA detection, market, ops
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scanner_app;
```

Then point the **application** at `scanner_app` and leave **migrations** running
as `scanner`: alembic needs DDL, and the whole arrangement collapses if the
process that publishes signals is also the one that can drop the triggers.

Order matters on the switch — migrate first, then restart the app on the new
credential. Reversing it starts an engine that cannot create the tables it is
about to write to.

After the switch, `immutability_grant_layer_absent` should stop appearing in
the engine logs. If it still does, the app is still connecting as the owner and
nothing has changed but the password.


## Step 10 — label the build before the soak

`SCANNER_RELEASE` in `ops/env/dev.env` stamps every log line and the
`scanner_process_info` metric. It read `soak` for the whole of the first soak,
which is precisely why that soak could not be told apart from anything else
afterwards — the question "was this the build that ran 72 h?" had no answer in
the data.

Set it to something that names the build, before the clock starts:

```bash
sed -i "s|^SCANNER_RELEASE=.*|SCANNER_RELEASE=p1b-$(git rev-parse --short HEAD)|" ops/env/dev.env
$DC up -d --force-recreate engine worker ingest api
```

Then record T0 and check it took:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ > ~/soak_t0.txt
$DC exec -T engine python -c "import urllib.request;   print([l for l in urllib.request.urlopen('http://localhost:8002/internal/metrics',timeout=5).read().decode().splitlines() if l.startswith('scanner_process_info')][0])"
```

`curl` is not in the image — it is read-only and slim. Use `python` as above.

### The consumer group after a recreate looks alarming and is not

`XINFO CONSUMERS` will show several consumers, the newest holding the whole
pending batch with a large `idle`. That reads as "a dead consumer stranded 32
closes". Check before believing it:

```bash
docker inspect --format "{{.Id}}" scanner-dev-engine-1 | cut -c1-12
$DC exec -T redis redis-cli XINFO CONSUMERS scanner:stream:candle-closed engine | paste - - - - - - - -
```

The consumer name is `engine-$HOSTNAME`, and `$HOSTNAME` is the container id.
If the id matches the pending consumer, that batch is *in flight*, not
stranded — the engine simply does not talk to Redis while it works through
thirty-odd closes at twenty seconds each. What matters is that the **dead**
consumers show `pending 0`. On 2026-08-24 they did.


## Step 11 — the api process needs a signing secret (S10)

`SCANNER_ACCESS_TOKEN_SECRET` has **no default**, so the api container will
refuse to start without it. That is deliberate: a default would let the process
boot and issue access tokens anyone holding the default could forge, and the
symptom — everything works — is indistinguishable from correct operation.

Generate one and put it in the env file before deploying anything that includes
S10:

```bash
python3 -c "import secrets; print('SCANNER_ACCESS_TOKEN_SECRET=' + secrets.token_urlsafe(48))"   >> ops/env/dev.env
```

Minimum 32 characters; `token_urlsafe(48)` gives 64. Rotating it invalidates
every outstanding access token — refresh cookies survive, so clients recover on
their next refresh rather than being signed out.

**Do not deploy this during a soak.** It restarts the api container, and
`soak_status.sh` counts a restart on any of the six as a broken run. The
identity work is api-only and the soak is about the engine, so it waits.

### First account

There is no `/auth/register` — §18.1's row triggers a verification email and no
provider is configured. Provision the operator account instead:

```bash
SCANNER_NEW_PASSWORD='<a real passphrase>'   $DC run --rm api python -m scanner.runtime.cli users create --email you@example.com
```

The command takes no `--password` flag: argv is visible in `ps`, lands in shell
history, and is captured by process accounting. It reads the environment
variable above, or prompts when it is unset.
