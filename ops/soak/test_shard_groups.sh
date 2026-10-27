#!/usr/bin/env bash
# Tests for shard_groups.sh's parsers, shard_resume_point.py and
# deploy_gate.sh's multi-group lag reading. Pure: no docker, no redis.
set -u
cd "$(dirname "$0")"

. ./shard_groups.sh
. ./deploy_gate.sh

fails=0
check() {  # check <label> <want> <got>
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: want '$2', got '$3'"
    fails=$((fails + 1))
  fi
}

# redis-cli (no TTY) prints XINFO GROUPS as alternating key / value lines.
two_groups=$(printf '%s\n' \
  name engine-shard-0-of-2 consumers 1 pending 0 last-delivered-id 1761000000000-0 entries-read 10 lag 0 \
  name engine-shard-1-of-2 consumers 1 pending 2 last-delivered-id 1761000000500-3 entries-read 10 lag 3)
one_group=$(printf '%s\n' name engine consumers 1 pending 0 last-delivered-id 1761000000000-0 entries-read 9 lag 0)
nil_lag=$(printf '%s\n' name engine consumers 1 pending 0 last-delivered-id 1761000000000-0 entries-read 9 lag "")

check "names of two groups" "engine-shard-0-of-2 engine-shard-1-of-2" "$(printf '%s\n' "$two_groups" | group_names | tr '\n' ' ' | sed 's/ $//')"
check "a field of the second group" "1761000000500-3" "$(printf '%s\n' "$two_groups" | group_field engine-shard-1-of-2 last-delivered-id)"
check "a field of the first group" "0" "$(printf '%s\n' "$two_groups" | group_field engine-shard-0-of-2 pending)"

# The deploy gate must read every group, not the last one printed.
check "one group: lag/pending" "0 0" "$(printf '%s\n' "$one_group" | lag_pending_of)"
check "two groups: worst lag, summed pending" "3 2" "$(printf '%s\n' "$two_groups" | lag_pending_of)"
check "a nil lag is unknown, never 0" "unknown 0" "$(printf '%s\n' "$nil_lag" | lag_pending_of)"
check "no groups at all is unknown" "unknown unknown" "$(printf '' | lag_pending_of)"
stale_first=$(printf '%s\n' name engine consumers 1 pending 0 last-delivered-id 1-0 entries-read 1 lag 4120 \
  name engine-shard-0-of-2 consumers 1 pending 0 last-delivered-id 9-0 entries-read 9 lag 0)
check "a stale group listed FIRST still blocks the gate" "4120 0" "$(printf '%s\n' "$stale_first" | lag_pending_of)"

# Resume point: earliest over the groups, just before an oldest pending entry.
rp() { python3 shard_resume_point.py "$@"; }
check "one group, nothing pending" "1761000000500-3" "$(rp 1761000000500-3:-)"
check "pending wins: just before the oldest" "1761000000400-1" "$(rp 1761000000500-3:1761000000400-2)"
check "earliest of two groups" "1761000000000-0" "$(rp 1761000000000-0:- 1761000000500-3:-)"
check "a pending entry earlier than the other group" "1760999999999-18446744073709551615" "$(rp 1761000000500-3:1761000000000-0 1761000000200-0:-)"
check "sequence order within one millisecond" "5-2" "$(rp 5-9:- 5-2:-)"
check "millisecond beats sequence" "4-99" "$(rp 5-0:- 4-99:-)"
rp >/dev/null 2>&1; check "no groups refuses" "1" "$?"
rp not-an-id:- >/dev/null 2>&1; check "a malformed id refuses" "1" "$?"

[ "$fails" -eq 0 ] && echo "all shard group tests passed" || { echo "$fails failed"; exit 1; }
