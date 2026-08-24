# Runbook — Deploy P1b to the staging VM

## Purpose

Move the staging host from `008_outbox_events` to `013_param_set` and onto the
current build, without losing candles and without filling the disk.

This is not a routine deploy. **Five migrations run at once**, one of them
rebuilds a 15 GB table, and the host has 23 GB free.

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
| Pending | `009_trade_aggregates`, `010_wash_risk`, `011_interaction_identity`, `012_setups`, `013_param_set` |
| Free disk needed | **≥ 20 GB** before step 4 |

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

Expected: `008_outbox_events`, ~24.7M rows and rising, ~23 GB free.

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

### 5. Apply the migrations

```bash
time $DC run --rm -e SCANNER_DB_DSN="postgresql+asyncpg://scanner:scanner@db:5432/scanner" \
  api alembic upgrade head
```

Alembic wraps the whole upgrade in one transaction, so **009 through 013 are
atomic together**: any failure rolls all five back and leaves the database on
`008` with the original table intact. There is no half-migrated state to
repair.

Expect this to take minutes, not seconds — 011 sorts 24.7 M rows.

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

* revision `013_param_set`;
* row count down by roughly **20×** — around 1.2 M, not 24 M;
* duplicate triples: **0**;
* database materially smaller, free disk materially larger.

A row count that barely moved means the rebuild kept everything, and the
duplicate query is what proves it either way. Do not accept the count alone.

### 7. Rebuild the images and start

```bash
$DC build engine worker api ingest
$DC up -d
$DC ps
```

Expected: six containers, all `healthy` within a minute or two.

### 8. Confirm the parameter set registered

```bash
$PSQL -tAF '|' -c "select engine, version, param_set_version, left(checksum,12), deployed_at
                   from detection.algo_versions order by deployed_at desc limit 5"
```

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
| `alembic_version` | `013_param_set` | Migration rolled back; database is still on `008` and safe. Read the alembic error before retrying. |
| interaction rows | ~1.2 M | Unchanged ⇒ the rebuild did not run. Check the duplicate query before concluding anything. |
| duplicate triples | `0` | Non-zero ⇒ the unique index is missing; stop and investigate before starting the engine. |
| container restarts | `0` | Any restart ⇒ read that container's logs before restarting anything. |
| `ParameterSetMismatchError` | absent | Present ⇒ working as designed. Fix the parameter set, do not bypass. |
| stream `pending` | falls to ~0 | Stays high ⇒ engine behind; do not stop it, watch first. |

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
