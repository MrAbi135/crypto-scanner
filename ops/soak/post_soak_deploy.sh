#!/usr/bin/env bash
# The soak-end deploy, as a script instead of a memory.
#
# The batch waiting on this window is the 2026-08-29 domain review: PRs #192
# through #208, seventeen fixes across every engine, plus the SLS v1.0.8
# clarifications. This runs the sequence with an assertion at every step --
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
grep -qF 's4-v9' backend/src/scanner/application/detection/structure_replay.py   || fail "#203 missing: STRUCTURE_ALGO_VERSION is not s4-v9"
grep -qF 's5-v11' backend/src/scanner/application/detection/liquidity_replay.py   || fail "sweep-class fix missing: LIQUIDITY_ALGO_VERSION is not s5-v11"
grep -qF 's8-confluence-v29' backend/src/scanner/application/detection/confluence_replay.py   || fail "#206 missing: CONFLUENCE_ALGO_VERSION is not s8-confluence-v29"
grep -qF '_mature_recent_sweeps' backend/src/scanner/application/detection/liquidity_replay.py   || fail "#199 missing: sweeps never mature, so reclaimed/displaced/stop-hunt stay unreachable"
grep -qF 'def apply_recovery' backend/src/scanner/domain/structure/trend.py   || fail "#198 missing: the trend machine has no recovery edge"
grep -qF '_broken_premise' backend/src/scanner/application/detection/signal_monitor.py   || fail "#200/#204 missing: INVALIDATED_EARLY is still unreachable"
grep -qF 'abs(candles[cursor].high - candidate)' backend/src/scanner/domain/structure/swings.py   || fail "#203 missing: the swing walk-back still consumes higher candles"
grep -qF 's6-ob-v6' backend/src/scanner/application/detection/ict_ob_replay.py   || fail "#222 missing: ICT_OB_ALGO_VERSION is not s6-ob-v6"
grep -qF 'origin_opens = ob.created_at' backend/src/scanner/application/detection/ict_ob_replay.py   || fail "#222 missing: the OB helpers still index the window with frozen offsets"
grep -qF 'expire_only' backend/src/scanner/application/detection/liquidity_replay.py   || fail "liquidity version pin missing: the replay still sweeps and matures another version's pools"
grep -qF 'only_version=LIQUIDITY_ALGO_VERSION' backend/src/scanner/application/detection/confluence_replay.py   || fail "liquidity version pin missing: confluence still reads every liquidity generation"

echo "   all eleven batch markers present in the tree at $(git rev-parse --short HEAD)"

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

# What this batch is supposed to move, measured before it moves. Each is a
# count the old code could not change: the attribution ids were never written
# at all, and the four maturation event types did not exist as strings in the
# old engine. A number that does NOT move after the deploy means the fix is
# not running, whatever the greps in step 5 say about the file being present.
pre_null_ids=$($PSQL -c "
  select count(*)
    from detection.setups s,
         lateral json_each(s.evidence::json -> 'attribution') f,
         lateral json_array_elements(f.value) c
   where c.value ->> 'evidence_id' is null;" 2>/dev/null || echo "?")

pre_matured=$($PSQL -c "
  select count(*) from detection.engine_events
   where event_type in ('LIQUIDITY_SWEEP_RECLAIMED',
                        'LIQUIDITY_SWEEP_DISPLACED',
                        'LIQUIDITY_STOP_HUNT_FAILED',
                        'STRUCTURE_MSS_INVALIDATED_UP',
                        'STRUCTURE_MSS_INVALIDATED_DOWN');" 2>/dev/null || echo "?")

pre_setups=$($PSQL -c "select count(*) from detection.setups;")
pre_signals=$($PSQL -c "select count(*) from detection.signals;")

echo "   contributions with no evidence id : $pre_null_ids (every one, before v26)"
echo "   maturation events so far          : $pre_matured (zero -- the types are new)"
echo "   setups seen                       : $pre_setups"
echo "   signals published                 : $pre_signals"

[ "$pre_matured" = "0" ] || echo "   (note: maturation events already exist -- was the deploy already done?)"

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
step "4. Deploy -- this resets T0, which is the point"
# ---------------------------------------------------------------------------

$DC up -d || fail "compose up failed"
sleep 15

# ---------------------------------------------------------------------------
step "5. Verify the RUNNING containers, not the tree"
# ---------------------------------------------------------------------------

docker exec scanner-dev-engine-1 grep -qF 's5-v11' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine is not s5-v11 -- the image that started is not the image built"
docker exec scanner-dev-engine-1 grep -qF 's8-confluence-v29' /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine is not s8-confluence-v29"
docker exec scanner-dev-engine-1 grep -qF '_mature_recent_sweeps' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine does not mature sweeps"
docker exec scanner-dev-engine-1 grep -qF 'def apply_recovery' /app/src/scanner/domain/structure/trend.py   || fail "running engine has no trend recovery edge"
docker exec scanner-dev-engine-1 grep -qF '_broken_premise' /app/src/scanner/application/detection/signal_monitor.py   || fail "running engine cannot reach INVALIDATED_EARLY"
docker exec scanner-dev-engine-1 grep -qF 'abs(candles[cursor].high - candidate)' /app/src/scanner/domain/structure/swings.py   || fail "running engine still has the old swing walk-back"
docker exec scanner-dev-engine-1 grep -qF 'origin_opens = ob.created_at' /app/src/scanner/application/detection/ict_ob_replay.py   || fail "running engine still indexes the window with the OB's frozen offsets"
docker exec scanner-dev-engine-1 grep -qF 'expire_only' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine does not pin liquidity to its own version"
docker exec scanner-dev-engine-1 grep -qF 'only_version=LIQUIDITY_ALGO_VERSION' /app/src/scanner/application/detection/confluence_replay.py   || fail "running confluence still reads every liquidity generation"

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
   1. After ~30 min (a few M5/M15 passes), check the two counts MOVED. A file
      present in the container proves the image; only these prove the code:

        $PSQL -c "select count(*) from detection.engine_events where event_type in ('LIQUIDITY_SWEEP_RECLAIMED','LIQUIDITY_SWEEP_DISPLACED','LIQUIDITY_STOP_HUNT_FAILED','STRUCTURE_MSS_INVALIDATED_UP','STRUCTURE_MSS_INVALIDATED_DOWN');"
          -- was: $pre_matured   want: > 0 once a sweep or MSS matures

        $PSQL -c "select count(*) from detection.setups s, lateral json_each(s.evidence::json -> 'attribution') f, lateral json_array_elements(f.value) c where c.value ->> 'evidence_id' is not null;"
          -- was: 0 (of $pre_null_ids)   want: > 0 as NEW setups arrive

      Neither is instant: maturation needs a sweep with candles after it, and
      the id counts only move for setups written after this deploy. Old rows
      keep their nulls forever -- that is append-only working, not a failure.

   2. Invariants: the two false alarms are fixed in this same tree (check B
      read 'pool_id' where stop hunts write 'sweep_pool_id'; check A asked
      one of SLS 3.4's two idle conditions). Run:
        bash ops/soak/check_invariants.sh
      Expect: the ETHUSDT-H4 idle flag and the STOP_HUNT duplicate flag both
      GONE, with zero acknowledged lines. If either survives, the fix did not
      land -- do not acknowledge it, investigate it.

      Check A now reads its algo version off the RUNNING engine, so it cannot
      go blind on a version bump the way it would have on this one.

   3. Shakedown 2-4 hours: the :17 cron keeps running; read
      ~/soak-logs/alerts.log before trusting anything. It carried 167 fires
      across the last soak, every one of them from the two false alarms.

   4. The 72h clock restarted at:  $new_started

   5. Still deliberately NOT done here: SCANNER_INGEST_TRADES, and the 50
      ACTIVE symbols that ingest does not yet subscribe to. Both are their
      own step after a clean shakedown.
NEXT

echo
echo "OK -- deployed and verified against the running containers"
