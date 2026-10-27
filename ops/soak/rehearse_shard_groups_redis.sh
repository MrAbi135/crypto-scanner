#!/usr/bin/env bash
# Rehearsal of every consumer-group move speed step 1 makes, against a REAL
# Redis in a throwaway container -- not the VM's. Run on any machine with
# docker:   bash ops/soak/rehearse_shard_groups_redis.sh
#
# Not a CI test (it pulls an image and starts a container); the pure parts are
# in test_shard_groups.sh. This exercises what those cannot: redis-cli's real
# replies to XINFO / XPENDING / XGROUP, read by shard_groups.sh's own functions.
#
# The story, in the order the deploy and shards_off.sh run it:
#   A. one engine has read 8 closes and finished 6 (2 pending);
#   B. the deploy's pre-up cleanup removes a stale shard group, keeps `engine`;
#   C. each shard group is created where `engine` stands (the engine's rule:
#      just before the oldest pending), then `engine` is retired;
#   D. the shards move on unevenly, one with a close unfinished;
#   E. shards_off's move: `engine` is created where the slower shard stands,
#      and the shard groups go.
set -uo pipefail
cd "$(dirname "$0")"

export REDIS_CONTAINER=shardtest-redis-$$
export CANDLE_STREAM=rehearsal:candles
. ./shard_groups.sh
. ./deploy_gate.sh

fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: want '$2', got '$3'"; fails=$((fails + 1)); fi; }
cleanup() { docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1; }
trap cleanup EXIT

docker run -d --name "$REDIS_CONTAINER" redis:7.2.5 >/dev/null || { echo "cannot start redis"; exit 2; }
for _ in $(seq 1 30); do [ "$(redis_cli PING)" = "PONG" ] && break; sleep 1; done

for i in $(seq 0 11); do redis_cli XADD "$CANDLE_STREAM" "$((1000 + i))-1" n "$i" >/dev/null; done

# A. one engine, 8 delivered, 6 finished
redis_cli XGROUP CREATE "$CANDLE_STREAM" engine 0 >/dev/null
redis_cli XREADGROUP GROUP engine e1 COUNT 8 STREAMS "$CANDLE_STREAM" ">" >/dev/null
for i in 0 1 2 3 4 5; do redis_cli XACK "$CANDLE_STREAM" engine "$((1000 + i))-1" >/dev/null; done
check "A: engine's oldest pending" "1006-1" "$(oldest_pending engine)"
check "A: gate reads one group" "4 2" "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | lag_pending_of)"

# B. a stale shard group from an earlier switch; the pre-up cleanup removes it
redis_cli XGROUP CREATE "$CANDLE_STREAM" engine-shard-1-of-2 1001-1 >/dev/null
for g in $SHARD_GROUPS; do
  stream_groups | grep -qx "$g" && redis_cli XGROUP DESTROY "$CANDLE_STREAM" "$g" >/dev/null
done
check "B: only engine left" "engine" "$(stream_groups | tr '\n' ' ' | sed 's/ $//')"

# C. shards created where engine stands (event_consumer._resume_point), engine retired
seed=$(python3 shard_resume_point.py "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | group_field engine last-delivered-id):$(oldest_pending engine)")
check "C: seed is just before engine's oldest pending" "1006-0" "$seed"
for g in $SHARD_GROUPS; do redis_cli XGROUP CREATE "$CANDLE_STREAM" "$g" "$seed" >/dev/null; done
check "C: both shard groups exist" "2" "$(stream_groups | grep -cxE 'engine-shard-[01]-of-2')"
redis_cli XGROUP DESTROY "$CANDLE_STREAM" engine >/dev/null
check "C: engine retired" "engine-shard-0-of-2 engine-shard-1-of-2" "$(stream_groups | tr '\n' ' ' | sed 's/ $//')"
# A group created at an explicit id has an unknown entries-read, so Redis
# reports its lag as nil until it reads the stream's last entry. The gate must
# read that as "unknown" -- never 0 -- and it does.
check "C: a freshly created group's lag is unknown, not 0" "unknown 0" "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | lag_pending_of)"

# D. shard 0 finishes 4 closes; shard 1 reads 2 and finishes 1
redis_cli XREADGROUP GROUP engine-shard-0-of-2 s0 COUNT 4 STREAMS "$CANDLE_STREAM" ">" >/dev/null
for i in 6 7 8 9; do redis_cli XACK "$CANDLE_STREAM" engine-shard-0-of-2 "$((1000 + i))-1" >/dev/null; done
redis_cli XREADGROUP GROUP engine-shard-1-of-2 s1 COUNT 2 STREAMS "$CANDLE_STREAM" ">" >/dev/null
redis_cli XACK "$CANDLE_STREAM" engine-shard-1-of-2 1006-1 >/dev/null
check "D: shard 1's oldest pending" "1007-1" "$(oldest_pending engine-shard-1-of-2)"
check "D: shard 0 has nothing pending" "-" "$(oldest_pending engine-shard-0-of-2)"
check "D: still unknown while neither shard has reached the end" "unknown 1" "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | lag_pending_of)"
# Both shards catch up to the newest close, as they do every five minutes:
redis_cli XREADGROUP GROUP engine-shard-0-of-2 s0 COUNT 10 STREAMS "$CANDLE_STREAM" ">" >/dev/null
for i in 10 11; do redis_cli XACK "$CANDLE_STREAM" engine-shard-0-of-2 "$((1000 + i))-1" >/dev/null; done
redis_cli XREADGROUP GROUP engine-shard-1-of-2 s1 COUNT 10 STREAMS "$CANDLE_STREAM" ">" >/dev/null
for i in 8 9 10 11; do redis_cli XACK "$CANDLE_STREAM" engine-shard-1-of-2 "$((1000 + i))-1" >/dev/null; done
check "D: once both reach the end the lag is known again: 0, with 1007-1 still pending" "0 1" "$(redis_cli XINFO GROUPS "$CANDLE_STREAM" | lag_pending_of)"

# E. shards_off's move
point=$(single_engine_resume_point)
check "E: engine resumes just before shard 1's unfinished close" "1007-0" "$point"
check "E: create" "OK" "$(redis_cli XGROUP CREATE "$CANDLE_STREAM" engine "$point")"
for g in $SHARD_GROUPS; do redis_cli XGROUP DESTROY "$CANDLE_STREAM" "$g" >/dev/null; done
check "E: only engine left" "engine" "$(stream_groups | tr '\n' ' ' | sed 's/ $//')"
first=$(redis_cli XREADGROUP GROUP engine e2 COUNT 1 STREAMS "$CANDLE_STREAM" ">" | sed -n 2p)
check "E: the single engine's first close is shard 1's unfinished one" "1007-1" "$first"

echo
[ "$fails" -eq 0 ] && echo "REHEARSAL OK -- every group move behaves on a real Redis" || { echo "$fails failed"; exit 1; }
