#!/usr/bin/env bash
# G1b soak status. Roadmap §9: engine unattended >= 72h, every close gets a pass.
#
# This script lived only on the VM until 2026-08-25, which is part of why the
# version it replaces was wrong for thirteen hours without anyone reviewing it.
#
# What it missed, and why the failure it missed was invisible:
#
#   * `faults` grepped `consumer_crashed|redis_unavailable`. The engine's actual
#     failure mode logs `detection_pass_failed` at level error, so every pass
#     could fail while this printed `faults: 0`.
#   * `passes` counts completed passes since the container started. It only ever
#     goes up, so a stalled engine keeps reporting whatever it managed before it
#     stalled.
#   * Nothing measured *when* the last pass happened. That is the one number
#     that separates "working" from "stopped": on 2026-08-24 the last pass was
#     five hours old while every container reported healthy and the counter
#     read 336.
#
# Exit code is 0 only when nothing is flagged, so this can be run from cron or
# a watchdog rather than read by eye.

set -uo pipefail

cd ~/crypto-scanner || exit 2
C="docker compose -f ops/compose/docker-compose.dev.yml"
LOG="docker logs scanner-dev-engine-1"

# A pass older than this means the engine has stopped doing its job. The
# fastest timeframe closes every 5 minutes and a pass took 82s at its worst, so
# 20 minutes is several missed closes -- long enough not to fire on one slow
# batch, short enough to catch a stall the same hour it starts.
STALE_AFTER_MIN=20

problems=0
flag() { echo "  !! $*"; problems=$((problems + 1)); }

t0=$(cat ~/soak_t0.txt 2>/dev/null)
echo "T0:  ${t0:-unknown}"
echo "now: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "-- containers (restarts must stay 0) --"
for c in db redis ingest engine worker api; do
  n="scanner-dev-${c}-1"
  status=$(docker inspect -f "{{.State.Status}}" "$n" 2>/dev/null)
  restarts=$(docker inspect -f "{{.RestartCount}}" "$n" 2>/dev/null)
  printf "%-8s %s restarts=%s\n" "$c" "${status:-MISSING}" "${restarts:-?}"
  [ "$status" != "running" ] && flag "$c is ${status:-MISSING}"
  [ "${restarts:-0}" != "0" ] && flag "$c restarted ${restarts} times"
done
echo

echo "-- last completed pass --"
last=$($LOG 2>&1 | grep -a detection_pass_completed | tail -1)

if [ -z "$last" ]; then
  flag "no detection pass has ever completed"
else
  # The log line's own timestamp, not the container's clock: a pass that
  # completed is dated by when it completed.
  ts=$(echo "$last" | grep -oE '"timestamp": "[^"]+"' | tail -1 | cut -d'"' -f4)
  age_s=$(( $(date -u +%s) - $(date -u -d "$ts" +%s 2>/dev/null || echo 0) ))
  age_min=$(( age_s / 60 ))

  echo "$last" | cut -c1-200
  echo "age: ${age_min} min"

  # The check the old script did not have.
  if [ "$age_min" -ge "$STALE_AFTER_MIN" ]; then
    flag "last pass is ${age_min} min old (>= ${STALE_AFTER_MIN}); the engine is up but not working"
  fi
fi
echo

echo "-- stream --"
groups=$($C exec -T redis redis-cli XINFO GROUPS scanner:stream:candle-closed 2>/dev/null | paste - - - - - - - -)
echo "$groups" | tr '\t' ' '
lag=$(echo "$groups" | grep -oE "lag [0-9]+" | grep -oE "[0-9]+")

if ! echo "${lag:-x}" | grep -qE '^[0-9]+$'; then
  # A non-numeric reading is itself an alert. Silently skipping it is how the
  # backlog alarm in an earlier version of this script could never fire.
  flag "could not read stream lag (got '${lag:-empty}')"
elif [ "$lag" -gt 60 ]; then
  flag "stream lag is ${lag}; the engine is behind the closes"
fi
echo

echo "-- engine counters (since container start) --"
passes=$($LOG 2>&1 | grep -ac detection_pass_completed)
failed=$($LOG 2>&1 | grep -ac detection_pass_failed)
crashes=$($LOG 2>&1 | grep -acE "consumer_crashed|redis_unavailable")
errors=$($LOG 2>&1 | grep -ac '"level": "error"')

echo "passes:            ${passes}"
echo "failed passes:     ${failed}"
echo "consumer faults:   ${crashes}"
echo "error-level lines: ${errors}"

[ "$failed" -gt 0 ] && flag "${failed} detection passes have failed"
[ "$crashes" -gt 0 ] && flag "${crashes} consumer faults"
echo

echo "-- zone map (the §5.1 bound the interaction path was missing) --"
docker exec -i scanner-dev-db-1 psql -U scanner -d scanner -tAF"|" -c \
  "select symbol, timeframe, count(*) from detection.ict_zones
   where state not in ('INVALIDATED','EXPIRED','FILLED','INVERTED','DEAD')
   group by 1,2 order by 3 desc limit 4" 2>/dev/null
echo

if [ "$problems" -eq 0 ]; then
  echo "OK - nothing flagged"
  exit 0
fi

echo "${problems} PROBLEM(S) FLAGGED ABOVE"
exit 1
