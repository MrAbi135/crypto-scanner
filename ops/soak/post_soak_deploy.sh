#!/usr/bin/env bash
# The soak-end deploy, as a script instead of a memory.
#
# Three engine PRs wait on the 72-hour window: #148 (window-local MSS span),
# #179 (§4.2 touches), #185 (§5.6 BPR parent state). This runs the sequence
# the pending-actions note describes, with an assertion at every step --
# because the last two deploy days each lost hours to a step that "ran" and
# did nothing: a patch whose replace matched nothing, a deploy that rebuilt
# one image of four, a release stamp that described the checkout rather than
# the binary. Every check here is against the RUNNING artifact, not the
# working tree.
#
# What it deliberately does NOT do:
#   * remove the acknowledged.txt lines -- that is a git change (a prepared PR
#     removes them); merging it before the deploy would set the hourly cron
#     firing on defects that are known and queued. Merge it AFTER step 6
#     proves the counts moved, then `git pull` here.
#   * enable SCANNER_INGEST_TRADES -- the aggTrade stream is Binance's
#     highest-volume subscription, and turning it on in the same window as an
#     engine deploy puts two variables into one shakedown. Do it as its own
#     step after a clean shakedown, if at all.
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

# The three fixes must be in the tree we are about to build. Greps name the
# code, not commit hashes, because a squash merge rewrites hashes.
grep -q 's6-v3' backend/src/scanner/application/detection/ict_replay.py \
  || fail "#185 missing: ICT_ALGO_VERSION is not s6-v3"
grep -q 'count_pool_touches' backend/src/scanner/application/detection/liquidity_replay.py \
  || fail "#179 missing: touches are not counted"
grep -q 'def _within_mss' backend/src/scanner/application/detection/ict_ob_replay.py \
  || fail "#148 missing: MSS span helper not present"
grep -q 'at: datetime' backend/src/scanner/application/detection/ict_ob_replay.py \
  || fail "#148 missing: _within_mss does not take a datetime"

echo "   all three engine fixes present in the tree at $(git rev-parse --short HEAD)"

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

pre_touches=$($PSQL -c "select count(distinct evidence::json->'strength_components'->>'touches') from detection.liquidity_pools;")
# count(*), not a sum over the wrong column: the first draft summed a
# predicate on the whole array and read 0 against a host where check E says
# 88 of 88. Verified against the live database before this shipped.
pre_e=$($PSQL -c "select count(*) from detection.setups s, lateral json_each(s.evidence::json -> 'archetype_unmet') a, lateral json_array_elements(a.value) t where a.key = 'A1' and t.value #>> '{}' = 'mss_origin_zone_retested';" 2>/dev/null || echo "?")
pre_setups=$($PSQL -c "select count(*) from detection.setups;")
pre_bpr_v2=$($PSQL -c "select count(*) from detection.ict_zones where zone_type = 'BPR';")

echo "   distinct touches values : $pre_touches (should be 1 -- the defect)"
echo "   setups seen             : $pre_setups"
echo "   BPR zones (all versions): $pre_bpr_v2"

[ "$pre_touches" = "1" ] || echo "   (note: touches already varies -- was the deploy already done?)"

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

docker exec scanner-dev-engine-1 grep -q 's6-v3' /app/src/scanner/application/detection/ict_replay.py \
  || fail "running engine does not carry s6-v3 -- the image that started is not the image built"
docker exec scanner-dev-engine-1 grep -q 'count_pool_touches' /app/src/scanner/application/detection/liquidity_replay.py \
  || fail "running engine does not carry the touches fix"
docker exec scanner-dev-engine-1 grep -q 'at: datetime' /app/src/scanner/application/detection/ict_ob_replay.py \
  || fail "running engine does not carry the MSS span fix"

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
   1. After ~30 min (a few M5/M15 passes), check the counts moved:

        $PSQL -c "select count(distinct evidence::json->'strength_components'->>'touches') from detection.liquidity_pools;"
          -- was: $pre_touches   want: > 1

      Check E's ratio (was $pre_e unmet of $pre_setups setups) falls only as
      NEW setups arrive; do not expect an instant drop.

   2. When both have moved, merge the prepared acknowledged-lines PR, then:
        git pull && bash ops/soak/check_invariants.sh
      Expect: exit 0 with ZERO acknowledged lines. If it fires
      "acknowledgement matched nothing", the PR was merged before the deploy
      proved itself -- revert to investigate.

   3. Shakedown 2-4 hours: the :17 cron keeps running; read
      ~/soak-logs/alerts.log before trusting anything.

   4. The 72h clock restarted at:  $new_started
NEXT

echo
echo "OK -- deployed and verified against the running containers"
