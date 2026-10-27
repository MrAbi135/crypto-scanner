#!/usr/bin/env bash
# Speed step 1's way back: from two engine shards to one engine.
#
# Usage, on the VM:
#   bash ops/soak/shards_off.sh                                  # dry run: reads, prints, changes nothing
#   CONFIRM=shards-off REASON='...' bash ops/soak/shards_off.sh  # the real switch (resets T0)
#
# Why a script and not "edit the compose file and up -d": the single engine
# reads group `engine`, which post_soak_deploy.sh retired when the shards
# started. Re-created at "0" it would re-run every close the stream holds;
# re-created at "$" it would skip whatever a shard had not finished. It must
# start where the SLOWER shard stands -- just before its oldest unfinished
# close (shard_resume_point.py). That point is only stable once both shards
# have stopped, so the order below matters:
#   1. stop both engines        (nothing moves the shard groups any more)
#   2. create `engine` at the resume point, read after the stop
#   3. destroy the two shard groups (only the current topology's groups exist)
#   4. start one engine with shard count 1 (docker-compose.shards-off.yml)
#
# Re-running a close a faster shard already finished is safe: every detection
# write is idempotent and a decided candle is not decided again.

set -uo pipefail

cd ~/crypto-scanner || exit 2

. ops/soak/shard_groups.sh || { echo "!! ops/soak/shard_groups.sh is missing"; exit 1; }

DC="docker compose -f ops/compose/docker-compose.dev.yml -f ops/compose/docker-compose.shards-off.yml"

fail() { echo "!! $*"; exit 1; }
step() { echo; echo "== $*"; }

# ---------------------------------------------------------------------------
step "0. Preconditions (read-only)"
# ---------------------------------------------------------------------------

[ "$(docker inspect --format '{{.State.Running}}' scanner-dev-engine-shard1-1 2>/dev/null)" = "true" ]   || fail "engine-shard1 is not running -- this is not a two-shard deployment"
[ "$(docker exec scanner-dev-engine-1 printenv SCANNER_ENGINE_SHARD_COUNT 2>/dev/null | tr -d '\r')" = "2" ]   || fail "scanner-dev-engine-1 is not shard 0 of 2"

groups=$(stream_groups)
echo "   groups now: $(printf '%s\n' "$groups" | tr '\n' ' ')"
for g in $SHARD_GROUPS; do
  printf '%s\n' "$groups" | grep -qx "$g" || fail "group $g is missing"
done
printf '%s\n' "$groups" | grep -qx engine && fail "group 'engine' already exists -- a half-finished earlier switch? Investigate before touching it"

info=$(redis_cli XINFO GROUPS "$CANDLE_STREAM")
for g in $SHARD_GROUPS; do
  echo "   $g: last-delivered $(printf '%s\n' "$info" | group_field "$g" last-delivered-id), oldest pending $(oldest_pending "$g"), lag $(printf '%s\n' "$info" | group_field "$g" lag)"
done
preview=$(single_engine_resume_point) || fail "cannot compute the resume point"
echo "   'engine' would start at: $preview (re-read after the engines stop)"

if [ "${CONFIRM:-}" != "shards-off" ]; then
  echo
  echo "DRY RUN -- nothing changed. To switch: CONFIRM=shards-off REASON='...' bash ops/soak/shards_off.sh"
  exit 0
fi

[ -n "${REASON:-}" ] || fail "REASON is required: going back to one engine resets T0, so it is said out loud"

# ---------------------------------------------------------------------------
step "1. Stop both engines"
# ---------------------------------------------------------------------------
echo "   reason: $REASON"
docker stop scanner-dev-engine-shard1-1 scanner-dev-engine-1 >/dev/null </dev/null || fail "could not stop the engines"
for e in scanner-dev-engine-1 scanner-dev-engine-shard1-1; do
  [ "$(docker inspect --format '{{.State.Running}}' "$e")" = "false" ] || fail "$e is still running"
done

# ---------------------------------------------------------------------------
step "2. Create group 'engine' where the slower shard stands"
# ---------------------------------------------------------------------------
point=$(single_engine_resume_point) || fail "cannot compute the resume point -- both engines are stopped; start them again with: docker start scanner-dev-engine-1 scanner-dev-engine-shard1-1"
[ "$(redis_cli XGROUP CREATE "$CANDLE_STREAM" engine "$point")" = "OK" ]   || fail "XGROUP CREATE engine $point failed -- both engines are stopped"
[ "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | group_field engine last-delivered-id)" = "$point" ]   || fail "group 'engine' is not at $point"
echo "   'engine' created at $point"

# ---------------------------------------------------------------------------
step "3. Destroy the two shard groups"
# ---------------------------------------------------------------------------
for g in $SHARD_GROUPS; do
  [ "$(redis_cli XGROUP DESTROY "$CANDLE_STREAM" "$g")" = "1" ] || fail "could not destroy $g"
done
[ "$(stream_groups | tr '\n' ' ')" = "engine " ] || fail "groups after the switch are not exactly 'engine': $(stream_groups | tr '\n' ' ')"
echo "   groups now: engine"

# ---------------------------------------------------------------------------
step "4. Start one engine (shard count 1)"
# ---------------------------------------------------------------------------
docker rm scanner-dev-engine-shard1-1 >/dev/null </dev/null || fail "could not remove scanner-dev-engine-shard1-1"
$DC up -d --no-deps engine </dev/null || fail "compose up of the single engine failed"

waited=0
until docker logs scanner-dev-engine-1 --since 2m 2>&1 | grep engine_consumer_started | grep -q '"group": "engine"'; do
  [ "$waited" -ge 90 ] && fail "the engine did not start consuming group 'engine' within 90 s"
  sleep 5
  waited=$((waited + 5))
done
[ "$(docker exec scanner-dev-engine-1 printenv SCANNER_ENGINE_SHARD_COUNT 2>/dev/null | tr -d '\r')" = "1" ]   || fail "the engine is not running with shard count 1"
docker inspect scanner-dev-engine-shard1-1 >/dev/null 2>&1 && fail "engine-shard1 still exists"

new_started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1)
mkdir -p ~/soak-logs
echo "$new_started" > ~/soak-logs/T0
echo "   one engine consuming 'engine' from $point; new T0 $new_started"

cat <<NEXT

   The base compose file still says TWO shards. The next post_soak_deploy.sh
   switches them back on, re-seeding both shard groups from 'engine'. To stay
   on one engine, change ops/compose/docker-compose.dev.yml in a PR first.

OK -- one engine
NEXT
