#!/usr/bin/env bash
# The soak-end deploy, as a script instead of a memory.
#
# The step-0 and step-5 markers name the look-ahead audit bundle, PRs #265
# through #279 (deployed 2026-09-15), which also brought migration 022. This
# runs the sequence with an assertion at every step --
# because the deploy days before it each lost hours to a step that "ran" and
# did nothing: a patch whose replace matched nothing, a deploy that rebuilt
# one image of four, a release stamp that described the checkout rather than
# the binary. Every check here is against the RUNNING artifact, not the
# working tree.
#
# The step-0 and step-5 greps are REWRITTEN PER BATCH, on purpose. Left
# naming the previous batch they rot in the worse of two directions: 's6-v3'
# blocked this very deploy once ict_replay reached s6-v4, and a grep that
# still matches proves only that last month's fix is present.
#
# What it deliberately does NOT do:
#   * enable SCANNER_INGEST_TRADES -- the aggTrade stream is Binance's
#     highest-volume subscription, and turning it on in the same window as an
#     engine deploy puts two variables into one shakedown. Do it as its own
#     step after a clean shakedown, if at all.
#   * subscribe ingest to the 50 symbols that reached ACTIVE on 2026-09-07.
#     Same reason: one variable per shakedown.
#
# Usage, on the VM:   bash ops/soak/post_soak_deploy.sh
# From Windows:       ./ops/soak/post_soak_deploy.ps1

set -uo pipefail

cd ~/crypto-scanner || exit 2
DC="docker compose -f ops/compose/docker-compose.dev.yml"
PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner -At"

fail() { echo "!! $*"; exit 1; }
step() { echo; echo "== $*"; }

# ---------------------------------------------------------------------------
step "0. Preconditions -- refuse to run rather than half-run"
# ---------------------------------------------------------------------------

started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1 2>/dev/null)   || fail "engine container not found"

# The soak is a period of time, and it began when the last deploy recorded T0
# -- not when this container happened to start. Measuring from StartedAt was
# wrong in both directions: a container restarted an hour ago reads as a soak
# that never ran, and a soak that finished reads as unfinished the moment
# anything restarts the engine.
soak_start=${SOAK_T0_FILE:-$HOME/soak-logs/T0}

if [ -r "$soak_start" ]; then
  t0=$(cat "$soak_start")
else
  t0=$started
  echo "   (no recorded T0 at $soak_start -- measuring from the container)"
fi

t0_s=$(date -u -d "$t0" +%s)
started_s=$(date -u -d "$started" +%s)
now_s=$(date -u +%s)
elapsed_h=$(( (now_s - t0_s) / 3600 ))

echo "   soak T0     : $t0  (${elapsed_h}h ago)"
echo "   engine start: $started"

# Cutting a soak short is sometimes the owner's call -- a fix worth more than
# the hours left on the clock. It is allowed only out loud, the same way a
# restart is: without a declared reason the gate still refuses, and with one
# the reason is printed into the deploy log next to the hours thrown away.
# Faking T0 instead would put a lie in ~/soak-logs/T0 that every later check
# would read as truth.
if [ "$elapsed_h" -lt 72 ]; then
  if [ -z "${EARLY_DEPLOY_REASON:-}" ]; then
    fail "soak is at ${elapsed_h}h of 72 -- this script exists so nobody resets it early. Set EARLY_DEPLOY_REASON='...' only if the owner approved ending it now."
  fi

  echo "   !! soak ENDED EARLY at ${elapsed_h}h of 72, declared: $EARLY_DEPLOY_REASON"
fi

# RestartCount cannot see this. `docker kill` followed by `docker start` --
# exactly what the G1b resume proof does, and what a human debugging a wedged
# engine does -- leaves the counter at 0 while the container plainly
# restarted. Measured 2026-09-11: SIGKILL, start, RestartCount still 0. The
# durable signal is a start time sitting after T0, so that is what is asked,
# and a deliberate restart has to say so out loud.
if [ "$started_s" -gt $(( t0_s + 120 )) ]; then
  if [ -z "${RESTART_REASON:-}" ]; then
    fail "engine started $(( (started_s - t0_s) / 60 )) minutes after T0 -- the soak was interrupted. Set RESTART_REASON='...' if that was deliberate."
  fi

  echo "   restart since T0, declared: $RESTART_REASON"
fi

restarts=$(docker inspect --format '{{.RestartCount}}' scanner-dev-engine-1)
[ "$restarts" = "0" ] || fail "engine restarted $restarts times during the soak -- investigate before deploying on top"

git fetch -q origin || fail "git fetch failed"
git diff --quiet && git diff --cached --quiet || fail "working tree is dirty"

local_rev=$(git rev-parse main)
remote_rev=$(git rev-parse origin/main)
[ "$local_rev" = "$remote_rev" ] || git pull -q || fail "git pull failed"

# The batch must be in the tree we are about to build. Greps name the code,
# not commit hashes, because a squash merge rewrites hashes -- and they are
# rewritten for every batch, because a grep naming the LAST batch either fails
# (blocking a good deploy: 's6-v3' did exactly that once ict_replay reached
# s6-v4) or passes while proving nothing about what is being shipped.
grep -qF 's4-v10' backend/src/scanner/application/detection/structure_replay.py   || fail "#271 missing: STRUCTURE_ALGO_VERSION is not s4-v10 (structure facts in candle order)"
grep -qF 's6-structure-shift-v5' backend/src/scanner/application/detection/structure_shift_replay.py   || fail "#270 missing: the shift engine does not resume"
grep -qF 's5-v16' backend/src/scanner/application/detection/liquidity_replay.py   || fail "lifecycle fix missing: LIQUIDITY_ALGO_VERSION is not s5-v16"
grep -qF 's6-v6' backend/src/scanner/application/detection/ict_replay.py   || fail "#272 missing: ICT_ALGO_VERSION is not s6-v6"
grep -qF 's6-ote-v7' backend/src/scanner/application/detection/ict_ote_replay.py   || fail "OTE finalization fix missing: ICT_OTE_ALGO_VERSION is not s6-ote-v7"
grep -qF 's6-ob-v8' backend/src/scanner/application/detection/ict_ob_replay.py   || fail "#272 missing: ICT_OB_ALGO_VERSION is not s6-ob-v8"
grep -qF 's6-interaction-v6' backend/src/scanner/application/detection/ict_interaction_replay.py   || fail "#275 missing: ICT_INTERACTION_ALGO_VERSION is not s6-interaction-v6"
grep -qF 's7-participation-v4' backend/src/scanner/application/detection/participation_replay.py   || fail "#273 missing: PARTICIPATION_ALGO_VERSION is not s7-participation-v4"
grep -qF 's8-confluence-v32' backend/src/scanner/application/detection/confluence_replay.py   || fail "signal gate missing: CONFLUENCE_ALGO_VERSION is not s8-confluence-v32"
grep -qF 'DEFAULT_SIGNAL_TIMEFRAMES' backend/src/scanner/application/detection/confluence_replay.py   || fail "signal gate missing: no fail-closed published-timeframe default"
grep -qF 'def first_undecided_index' backend/src/scanner/application/detection/state.py   || fail "#272 missing: engines still re-decide the whole window"
grep -qF 'CURRENT_EVENT_VERSIONS' backend/src/scanner/application/detection/event_versions.py   || fail "#274 missing: event reads are not pinned to the running versions"
grep -qF 'terminal_since' backend/src/scanner/application/detection/ict_interaction_replay.py   || fail "#275 missing: the killing candle's interactions are still dropped"
grep -qF 'def relative_volumes' backend/src/scanner/domain/common/rvol.py   || fail "#273 missing: RVOL still reads the window only"
grep -qF 'expire_only' backend/src/scanner/application/detection/liquidity_replay.py   || fail "liquidity version pin missing: the replay still sweeps another version's pools"
grep -qF 'only_version=LIQUIDITY_ALGO_VERSION' backend/src/scanner/application/detection/confluence_replay.py   || fail "liquidity version pin missing: confluence reads every liquidity generation"
grep -qF '_mature_recent_sweeps' backend/src/scanner/application/detection/liquidity_replay.py   || fail "#199 missing: sweeps never mature"
grep -qF 'def apply_recovery' backend/src/scanner/domain/structure/trend.py   || fail "#198 missing: the trend machine has no recovery edge"
grep -qF '_broken_premise' backend/src/scanner/application/detection/signal_monitor.py   || fail "#200/#204 missing: INVALIDATED_EARLY is unreachable"
grep -qF 'abs(candles[cursor].high - candidate)' backend/src/scanner/domain/structure/swings.py   || fail "#203 missing: the swing walk-back still consumes higher candles"
grep -qF 'origin_opens = ob.created_at' backend/src/scanner/application/detection/ict_ob_replay.py   || fail "#222 missing: the OB helpers index the window with frozen offsets"
grep -qF 'SUSPECT_COUNT_TIMEFRAMES' backend/src/scanner/application/marketdata/fake_volume_job.py   || fail "wash_risk fix missing: the suspect count still spans every timeframe (SLS v1.0.10)"
test -f backend/src/scanner/infrastructure/persistence/alembic/versions/022_recorded_at.py   || fail "#276 missing: migration 022_recorded_at is not in the tree"

# Owner ruling 2026-09-15 (M8 option B): M15/M5 must not publish. The code defaults to
# H1,H4; an override in the env file would open them, so its presence refuses the deploy.
! grep -q '^SCANNER_SIGNAL_TIMEFRAMES=' ops/env/dev.env   || fail "ops/env/dev.env sets SCANNER_SIGNAL_TIMEFRAMES -- the H1,H4 default is the approved set"

echo "   all 22 batch markers present, signal-timeframe override absent, tree at $(git rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
step "1. Invariants before touching anything (expect: exit 0, 1 acknowledged)"
# ---------------------------------------------------------------------------

bash ops/soak/check_invariants.sh > /tmp/pre_deploy_invariants.log 2>&1
pre_rc=$?
tail -3 /tmp/pre_deploy_invariants.log

[ "$pre_rc" -eq 0 ] || fail "invariants dirty BEFORE the deploy -- fix that first (log: /tmp/pre_deploy_invariants.log)"

# ---------------------------------------------------------------------------
step "2. Pre-deploy counts, so step 6 has something to compare against"
# ---------------------------------------------------------------------------

# Signals are split by timeframe because the signal gate (#278, owner ruling
# 2026-09-15) keeps M5 and M15 out of that table. A new row for either after
# this deploy means the gate is not what is running, whatever the step-5 greps
# say about the file being present.
pre_setups=$($PSQL -c "select count(*) from detection.setups;")
pre_signals=$($PSQL -c "select count(*) from detection.signals;")
pre_low_tf=$($PSQL -c "select count(*) from detection.signals where timeframe in ('M5','M15');")

echo "   setups seen               : $pre_setups"
echo "   signals published         : $pre_signals"
echo "   of which on M5 or M15     : $pre_low_tf"

# ---------------------------------------------------------------------------
step "2b. Stamp the release with the commit actually being built"
# ---------------------------------------------------------------------------
# SCANNER_RELEASE is hand-set in ops/env/dev.env and nothing ever updated it.
# On 2026-09-07 every log line still said p1b-1842b4d -- the commit of the
# deploy before last, nine days and twenty-four commits stale. The stamp is
# already documented as a liar; this is why it lies. Set from the tree being
# built, and asserted against the running container in step 5, it cannot
# drift again on its own.
release="p1b-$(git rev-parse --short HEAD)"

if grep -q '^SCANNER_RELEASE=' ops/env/dev.env; then
  sed -i "s|^SCANNER_RELEASE=.*|SCANNER_RELEASE=${release}|" ops/env/dev.env
else
  echo "SCANNER_RELEASE=${release}" >> ops/env/dev.env
fi

grep -q "^SCANNER_RELEASE=${release}$" ops/env/dev.env   || fail "could not stamp SCANNER_RELEASE=${release} into ops/env/dev.env"

echo "   release stamp: $release"

# ---------------------------------------------------------------------------
step "3. Build ALL FOUR images (the 2026-08-26 lesson: never just one)"
# ---------------------------------------------------------------------------

$DC build api engine worker ingest frontend || fail "build failed"

# ---------------------------------------------------------------------------
step "3b. Migrate the schema BEFORE the new code starts"
# ---------------------------------------------------------------------------
# On 2026-09-15 this script had no such step. The new engine started against a
# database still on 021, every pass of the first M5 close failed with
# UndefinedColumn recorded_at -- 30 detection_pass_failed -- and 022 was applied
# by hand five minutes later. The step-0 grep proved the migration FILE was in
# the tree; nothing proved it had run.
#
# Before `up`, not after: the old code keeps working on a migrated schema
# (migrations here are additive), the new code must never start on an old one,
# and a migration that fails here leaves the running containers untouched.
#
# The owner credential reaches this one-off container and nothing else (the
# grant layer, docs/runbooks/deploy-p1b.md): read from the db container inside
# a subshell, passed to compose by NAME so it is in no argument list and no
# log, and gone when the subshell exits. The long-running services keep the
# scanner_app DSN from ops/env/dev.env.

versions=backend/src/scanner/infrastructure/persistence/alembic/versions
revisions=$(cat "$versions"/*.py | tr -d '\r' | sed -n 's/^revision = "\(.*\)"$/\1/p' | sort)
parents=$(cat "$versions"/*.py | tr -d '\r' | sed -n 's/^down_revision = "\(.*\)"$/\1/p' | sort)
head_rev=$(comm -23 <(printf '%s\n' "$revisions") <(printf '%s\n' "$parents"))

[ -n "$head_rev" ] && [ "$(printf '%s\n' "$head_rev" | wc -l)" -eq 1 ]   || fail "the migrations in the tree do not have exactly one head: '${head_rev}'"

schema_rev=$($PSQL -c "select version_num from alembic_version;" | tr -d '\r')
echo "   schema: ${schema_rev:-?}   tree head: $head_rev"

if [ "$schema_rev" = "$head_rev" ]; then
  echo "   already at head -- nothing to migrate"
else
  (
    pw=$(docker exec scanner-dev-db-1 printenv POSTGRES_PASSWORD </dev/null) || exit 3
    [ -n "$pw" ] || exit 3
    export SCANNER_MIGRATION_DB_DSN="postgresql+asyncpg://scanner:${pw}@db:5432/scanner"
    unset pw
    $DC run --rm --no-deps -T -e SCANNER_MIGRATION_DB_DSN api alembic upgrade head </dev/null
  )
  migrate_rc=$?

  [ "$migrate_rc" -eq 0 ]   || fail "alembic upgrade head failed (exit $migrate_rc) -- nothing was restarted, the old containers still run"
fi

schema_rev=$($PSQL -c "select version_num from alembic_version;" | tr -d '\r')
[ "$schema_rev" = "$head_rev" ]   || fail "schema is at '$schema_rev' after migrating; the tree head is '$head_rev'"

echo "   schema at head: $schema_rev"

# ---------------------------------------------------------------------------
step "4. Deploy -- this resets T0, which is the point"
# ---------------------------------------------------------------------------

$DC up -d || fail "compose up failed"
sleep 15

# ---------------------------------------------------------------------------
step "5. Verify the RUNNING containers, not the tree"
# ---------------------------------------------------------------------------

docker exec scanner-dev-engine-1 grep -qF 's4-v10' /app/src/scanner/application/detection/structure_replay.py   || fail "running engine: STRUCTURE_ALGO_VERSION is not s4-v10 (structure facts in candle order)"
docker exec scanner-dev-engine-1 grep -qF 's6-structure-shift-v5' /app/src/scanner/application/detection/structure_shift_replay.py   || fail "running engine: the shift engine does not resume"
docker exec scanner-dev-engine-1 grep -qF 's5-v16' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine: LIQUIDITY_ALGO_VERSION is not s5-v16"
docker exec scanner-dev-engine-1 grep -qF 's6-v6' /app/src/scanner/application/detection/ict_replay.py   || fail "running engine: ICT_ALGO_VERSION is not s6-v6"
docker exec scanner-dev-engine-1 grep -qF 's6-ote-v7' /app/src/scanner/application/detection/ict_ote_replay.py   || fail "running engine: ICT_OTE_ALGO_VERSION is not s6-ote-v7"
docker exec scanner-dev-engine-1 grep -qF 's6-ob-v8' /app/src/scanner/application/detection/ict_ob_replay.py   || fail "running engine: ICT_OB_ALGO_VERSION is not s6-ob-v8"
docker exec scanner-dev-engine-1 grep -qF 's6-interaction-v6' /app/src/scanner/application/detection/ict_interaction_replay.py   || fail "running engine: ICT_INTERACTION_ALGO_VERSION is not s6-interaction-v6"
docker exec scanner-dev-engine-1 grep -qF 's7-participation-v4' /app/src/scanner/application/detection/participation_replay.py   || fail "running engine: PARTICIPATION_ALGO_VERSION is not s7-participation-v4"
docker exec scanner-dev-engine-1 grep -qF 's8-confluence-v32' /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine: CONFLUENCE_ALGO_VERSION is not s8-confluence-v32"
docker exec scanner-dev-engine-1 grep -qF 'DEFAULT_SIGNAL_TIMEFRAMES' /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine: no fail-closed published-timeframe default"
docker exec scanner-dev-engine-1 grep -qF 'def first_undecided_index' /app/src/scanner/application/detection/state.py   || fail "running engine: engines still re-decide the whole window"
docker exec scanner-dev-engine-1 grep -qF 'CURRENT_EVENT_VERSIONS' /app/src/scanner/application/detection/event_versions.py   || fail "running engine: event reads are not pinned to the running versions"
docker exec scanner-dev-engine-1 grep -qF 'terminal_since' /app/src/scanner/application/detection/ict_interaction_replay.py   || fail "running engine: the killing candle's interactions are still dropped"
docker exec scanner-dev-engine-1 grep -qF 'def relative_volumes' /app/src/scanner/domain/common/rvol.py   || fail "running engine: RVOL still reads the window only"
docker exec scanner-dev-engine-1 grep -qF 'expire_only' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine: the replay still sweeps another version's pools"
docker exec scanner-dev-engine-1 grep -qF 'only_version=LIQUIDITY_ALGO_VERSION' /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine: confluence reads every liquidity generation"
docker exec scanner-dev-engine-1 grep -qF '_mature_recent_sweeps' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine: sweeps never mature"
docker exec scanner-dev-engine-1 grep -qF 'def apply_recovery' /app/src/scanner/domain/structure/trend.py   || fail "running engine: the trend machine has no recovery edge"
docker exec scanner-dev-engine-1 grep -qF '_broken_premise' /app/src/scanner/application/detection/signal_monitor.py   || fail "running engine: INVALIDATED_EARLY is unreachable"
docker exec scanner-dev-engine-1 grep -qF 'abs(candles[cursor].high - candidate)' /app/src/scanner/domain/structure/swings.py   || fail "running engine: the swing walk-back still consumes higher candles"
docker exec scanner-dev-engine-1 grep -qF 'origin_opens = ob.created_at' /app/src/scanner/application/detection/ict_ob_replay.py   || fail "running engine: the OB helpers index the window with frozen offsets"
docker exec scanner-dev-worker-1 grep -qF 'SUSPECT_COUNT_TIMEFRAMES' /app/src/scanner/application/marketdata/fake_volume_job.py   || fail "running worker: the suspect count still spans every timeframe"
docker exec scanner-dev-engine-1 test -f /app/src/scanner/infrastructure/persistence/alembic/versions/022_recorded_at.py   || fail "running engine image has no migration 022_recorded_at"

schema_now=$($PSQL -c "select version_num from alembic_version;" | tr -d '\r')
[ "$schema_now" = "$head_rev" ]   || fail "the new code is running on schema '$schema_now'; the tree head is '$head_rev'"

running_release=$(docker exec scanner-dev-engine-1 printenv SCANNER_RELEASE 2>/dev/null | tr -d '
')
[ "$running_release" = "$release" ]   || fail "running engine reports release '$running_release', expected '$release'"

new_started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1)
[ "$new_started" != "$started" ] || fail "engine StartedAt did not change -- it was not restarted"

for c in scanner-dev-engine-1 scanner-dev-worker-1 scanner-dev-ingest-1 scanner-dev-api-1; do
  running=$(docker inspect --format '{{.State.Running}}' "$c")
  [ "$running" = "true" ] || fail "$c is not running after the deploy"
done

mkdir -p ~/soak-logs
echo "$new_started" > ~/soak-logs/T0
echo "   new T0: $new_started  (recorded in ~/soak-logs/T0)"

# ---------------------------------------------------------------------------
step "6. What to do next (the parts that need hours, not a script)"
# ---------------------------------------------------------------------------

cat <<NEXT
   1. After the first M5 close (~15 min), no pass may have failed. A schema
      or wiring defect shows here before anywhere else -- on 2026-09-15 it
      was 30 of these within five minutes:

        docker logs --since "$new_started" scanner-dev-engine-1 2>&1 | grep -c detection_pass_failed
          -- want: 0

   2. After the first-pass burst (~30 min), nothing may be left pending:

        docker exec scanner-dev-redis-1 redis-cli XPENDING scanner:stream:candle-closed engine
          -- want: a first field of 0

   3. The signal gate, for as long as the M5/M15 ruling stands:

        $PSQL -c "select count(*) from detection.signals where timeframe in ('M5','M15') and published_at >= '$new_started';"
          -- was: $pre_low_tf in all history   want: 0 new, ever

   4. Invariants, then a 2-4 hour shakedown. The :17 cron keeps running;
      read ~/soak-logs/alerts.log before trusting anything:

        bash ops/soak/check_invariants.sh
          -- want: exit 0. A new violation is investigated, never acknowledged
             to get the deploy through.

   5. The 72h clock restarted at:  $new_started   (schema: $head_rev)

   6. Still deliberately NOT done here: SCANNER_INGEST_TRADES, and widening
      the ingest subscription. Each is its own step after a clean shakedown.
NEXT

echo
echo "OK -- deployed and verified against the running containers"
