#!/usr/bin/env bash
# When may post_soak_deploy.sh run `up -d`? Sourced by it before step 4,
# and by test_deploy_gate.sh.
#
# deploy_window_ok <epoch_seconds> <stream_lag> <stream_pending>
#   prints one line "ok ..." or "wait: <reason>" and returns 0 / 1.
#
# Rules, each from a measured incident:
#   * 30-150 s after an M5 close: ingest is down ~9-21 s on a recreate (10-10 deploy),
#     and a close falling inside that gap arrives by backfill and is never scored
#     (09-27: containers started at 08:15:00, the M5+M15 close).
#   * the PREVIOUS close is M5-only: after an M15/H1/H4/D1 close the boundary's passes
#     run 43-117 s (boundary_last_pass_20261015.txt), so the engine is still busy.
#   * the NEXT close is M5-only, for the same reason one step later.
#     Together: only the windows after :05, :20, :35 and :50.
#   * not 23:50-00:29 UTC: the D1 close and the worker's universe loop (00:00-00:25).
#   * stream lag 0 and pending 0: nothing queued that a restart would leave waiting.
deploy_window_ok() {
  local now="$1" lag="$2" pending="$3"
  local phase=$(( now % 300 ))
  local prev=$(( now - phase ))
  local next=$(( prev + 300 ))
  local hm
  hm=$(date -u -d "@$now" +%H%M)

  if [ "$hm" \> 2349 ] || [ "$hm" \< 0030 ]; then
    echo "wait: 23:50-00:29 UTC (D1 close + universe loop)"; return 1
  fi
  if [ "$phase" -lt 30 ] || [ "$phase" -gt 150 ]; then
    echo "wait: ${phase}s after the M5 close, need 30-150"; return 1
  fi
  if [ $(( prev % 900 )) -eq 0 ]; then
    echo "wait: previous close $(date -u -d "@$prev" +%H:%M) is not M5-only"; return 1
  fi
  if [ $(( next % 900 )) -eq 0 ]; then
    echo "wait: next close $(date -u -d "@$next" +%H:%M) is not M5-only"; return 1
  fi
  if [ "$lag" != "0" ] || [ "$pending" != "0" ]; then
    echo "wait: stream lag=$lag pending=$pending, need 0/0"; return 1
  fi
  echo "ok: ${phase}s after the $(date -u -d "@$prev" +%H:%M) close; next close $(date -u -d "@$next" +%H:%M) is M5-only"
  return 0
}

# wait_for_deploy_window <max_wait_seconds>
#   Polls every 5 s until deploy_window_ok passes (prints the verdict, returns 0)
#   or max_wait_seconds have passed (prints the last reason, returns 1). The
#   clock, the sleep and the stream reading are functions so the tests can
#   drive them; the script uses the real ones below.
wait_for_deploy_window() {
  local max="$1" waited=0 lag pending verdict
  while :; do
    read -r lag pending <<< "$(stream_lag_pending)"
    if verdict=$(deploy_window_ok "$(gate_now)" "$lag" "$pending"); then
      echo "$verdict"
      return 0
    fi
    if [ "$waited" -ge "$max" ]; then
      echo "timeout after ${waited}s -- last: $verdict"
      return 1
    fi
    [ $(( waited % 60 )) -eq 0 ] && echo "$verdict"
    gate_sleep 5
    waited=$(( waited + 5 ))
  done
}

gate_now() { date -u +%s; }
gate_sleep() { sleep "$1"; }

# Live inputs, for the script: lag and pending of the engine group, read-only.
stream_lag_pending() {
  docker exec scanner-dev-redis-1 redis-cli XINFO GROUPS scanner:stream:candle-closed \
    | tr -d '\r' | awk 'p=="pending"{pd=$0} p=="lag"{lg=$0} {p=$0} END{print (lg==""?"unknown":lg), (pd==""?"unknown":pd)}'
}
