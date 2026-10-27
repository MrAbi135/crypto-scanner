#!/usr/bin/env bash
# The candle stream's consumer groups, read and moved for an engine topology
# switch (speed step 1, ops/compose/docker-compose.dev.yml). Sourced by
# post_soak_deploy.sh and shards_off.sh, and by test_shard_groups.sh.
#
# The rule both scripts keep: only the CURRENT topology's groups exist. One
# engine reads group `engine`. Two shards read `engine-shard-0-of-2` and
# `engine-shard-1-of-2`. A group left behind by the other topology stands where
# that topology stopped, so it is not an inert leftover:
#   * its lag grows for ever, and every check that reads "the" lag
#     (deploy_gate.sh, soak_status.sh) would wait on it or flag it;
#   * a later switch back would resume it from that old place and re-run
#     every close since.
# So each switch creates the incoming groups at the outgoing ones' place and
# then destroys the outgoing ones.

SHARD_GROUPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANDLE_STREAM="${CANDLE_STREAM:-scanner:stream:candle-closed}"
REDIS_CONTAINER="${REDIS_CONTAINER:-scanner-dev-redis-1}"
SHARD_GROUPS="engine-shard-0-of-2 engine-shard-1-of-2"

redis_cli() { docker exec "$REDIS_CONTAINER" redis-cli "$@" </dev/null | tr -d '\r'; }

# group_names < XINFO GROUPS output  -> one name per line
group_names() { awk 'p=="name"{print} {p=$0}'; }

# group_field <name> <field> < XINFO GROUPS output  -> that group's value
group_field() {
  awk -v want="$1" -v field="$2" '
    p=="name" {cur=$0}
    p==field && cur==want {print; exit}
    {p=$0}'
}

stream_groups() { redis_cli XINFO GROUPS "$CANDLE_STREAM" | group_names; }

# oldest_pending <group>  -> the group's oldest pending id, or "-" when none
oldest_pending() {
  local out
  out=$(redis_cli XPENDING "$CANDLE_STREAM" "$1")
  if [ "$(printf '%s\n' "$out" | sed -n 1p)" = "0" ]; then
    echo "-"
  else
    printf '%s\n' "$out" | sed -n 2p
  fi
}

# single_engine_resume_point  -> where group `engine` must start when the two
# shards hand back to one engine (shard_resume_point.py states the rule).
# Read-only. Fails if either shard group is missing.
single_engine_resume_point() {
  local info g delivered pending args=()
  info=$(redis_cli XINFO GROUPS "$CANDLE_STREAM")
  for g in $SHARD_GROUPS; do
    delivered=$(printf '%s\n' "$info" | group_field "$g" last-delivered-id)
    [ -n "$delivered" ] || { echo "no group $g" >&2; return 1; }
    pending=$(oldest_pending "$g")
    args+=("$delivered:$pending")
  done
  python3 "$SHARD_GROUPS_DIR/shard_resume_point.py" "${args[@]}"
}
