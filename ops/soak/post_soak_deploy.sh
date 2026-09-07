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

started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1 2>/dev/null) \
  || fail "engine container not found"

started_s=$(date -u -d "$started" +%s)
now_s=$(date -u +%s)
elapsed_h=$(( (now_s - started_s) / 3600 ))

echo "   engine up ${elapsed_h}h (since $started)"

if [ "$elapsed_h" -lt 72 ]; then
  fail "soak is at ${elapsed_h}h of 72 -- this script exists so nobody resets it early"
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
grep -qF 's5-v10' backend/src/scanner/application/detection/liquidity_replay.py   || fail "#203 missing: LIQUIDITY_ALGO_VERSION is not s5-v10"
grep -qF 's8-confluence-v29' backend/src/scanner/application/detection/confluence_replay.py   || fail "#206 missing: CONFLUENCE_ALGO_VERSION is not s8-confluence-v29"
grep -qF '_mature_recent_sweeps' backend/src/scanner/application/detection/liquidity_replay.py   || fail "#199 missing: sweeps never mature, so reclaimed/displaced/stop-hunt stay unreachable"
grep -qF 'def apply_recovery' backend/src/scanner/domain/structure/trend.py   || fail "#198 missing: the trend machine has no recovery edge"
grep -qF '_broken_premise' backend/src/scanner/application/detection/signal_monitor.py   || fail "#200/#204 missing: INVALIDATED_EARLY is still unreachable"
grep -qF 'abs(candles[cursor].high - candidate)' backend/src/scanner/domain/structure/swings.py   || fail "#203 missing: the swing walk-back still consumes higher candles"

echo "   all seven batch markers present in the tree at $(git rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
step "1. Invariants before touching anything (expect: exit 0, 2 acknowledged)"
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

docker exec scanner-dev-engine-1 grep -qF 's5-v10' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine is not s5-v10 -- the image that started is not the image built"
docker exec scanner-dev-engine-1 grep -qF 's8-confluence-v29' /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine is not s8-confluence-v29"
docker exec scanner-dev-engine-1 grep -qF '_mature_recent_sweeps' /app/src/scanner/application/detection/liquidity_replay.py   || fail "running engine does not mature sweeps"
docker exec scanner-dev-engine-1 grep -qF 'def apply_recovery' /app/src/scanner/domain/structure/trend.py   || fail "running engine has no trend recovery edge"
docker exec scanner-dev-engine-1 grep -qF '_broken_premise' /app/src/scanner/application/detection/signal_monitor.py   || fail "running engine cannot reach INVALIDATED_EARLY"
docker exec scanner-dev-engine-1 grep -qF 'abs(candles[cursor].high - candidate)' /app/src/scanner/domain/structure/swings.py   || fail "running engine still has the old swing walk-back"

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
