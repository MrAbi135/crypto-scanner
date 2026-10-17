#!/usr/bin/env bash
# Tests for deploy_window_ok at simulated times (runs in CI via the shell job).
set -uo pipefail
cd "$(dirname "$0")" || exit 2
. ./deploy_gate.sh

failures=0
t() {  # t <label> <utc time> <lag> <pending> <expected 0|1>
  local label="$1" when="$2" lag="$3" pending="$4" want="$5" out got
  out=$(deploy_window_ok "$(date -u -d "2026-10-16 $when" +%s)" "$lag" "$pending"); got=$?
  if [ "$got" = "$want" ]; then echo "  ok    $label -> $out"; else echo "  FAIL  $label (want $want) -> $out"; failures=$((failures+1)); fi
}

t ":00:30 after the H1 close"        "10:00:30" 0 0 1
t ":05:30 after an M5-only close"    "10:05:30" 0 0 0
t ":10:30 next close is :15 (M15)"   "10:10:30" 0 0 1
t ":15:30 after the M15 close"       "10:15:30" 0 0 1
t ":20:30 after an M5-only close"    "10:20:30" 0 0 0
t "23:55 blackout"                   "23:55:30" 0 0 1
t "00:10 blackout"                   "00:10:30" 0 0 1
t "23:50:30 blackout only (M5-only both sides)" "23:50:30" 0 0 1
t "00:20:30 blackout only (M5-only both sides)" "00:20:30" 0 0 1
t "00:35:00 exactly on the close"    "00:35:00" 0 0 1
t "00:35:30 first safe slot of day"  "00:35:30" 0 0 0
t "04:00:30 after the H4 close"      "04:00:30" 0 0 1
t ":50:30 after an M5-only close"    "10:50:30" 0 0 0
t ":05:29 one second too early"      "10:05:29" 0 0 1
t ":07:30 150 s, last good second"   "10:07:30" 0 0 0
t ":07:31 151 s, too late"           "10:07:31" 0 0 1
t ":05:30 but lag 3"                 "10:05:30" 3 0 1
t ":05:30 but pending 1"             "10:05:30" 0 1 1
t ":05:30 but lag unknown"           "10:05:30" unknown 0 1

echo
echo "waiting loop (simulated clock: gate_sleep advances it)"

# w <label> <start utc> <max wait s> <lag> <pending> <expected 0|1> [expected text]
w() {
  local label="$1" start="$2" max="$3" lag="$4" pending="$5" want="$6" text="${7:-}" out got
  out=$(
    SIM_NOW=$(date -u -d "2026-10-16 $start" +%s)
    gate_now() { echo "$SIM_NOW"; }
    gate_sleep() { SIM_NOW=$(( SIM_NOW + $1 )); }
    SIM_LAG="$lag" SIM_PENDING="$pending"
    stream_lag_pending() { echo "$SIM_LAG $SIM_PENDING"; }  # not $lag: the function under test has its own local
    wait_for_deploy_window "$max"
  ); got=$?
  if [ "$got" = "$want" ] && { [ -z "$text" ] || [[ "$out" == *"$text"* ]]; }; then
    echo "  ok    $label -> $(echo "$out" | tail -1)"
  else
    echo "  FAIL  $label (want $want${text:+, \"$text\"}) -> $out"; failures=$((failures+1))
  fi
}

w "10:00:30 waits for the 10:05 close"  "10:00:30" 900 0 0 0 "30s after the 10:05 close"
w "10:10:30 waits past :15 to :20"      "10:10:30" 900 0 0 0 "30s after the 10:20 close"
w "23:40 times out (no slot to 00:35)"  "23:40:00" 900 0 0 1 "timeout after 900s"
w "lag never clears times out"          "10:05:30" 900 4 0 1 "stream lag=4"
w "already in a window, no wait"        "10:06:00" 900 0 0 0 "60s after the 10:05 close"

echo
[ "$failures" -eq 0 ] && echo "OK -- gate holds" || echo "$failures failure(s)"
exit $(( failures > 0 ? 1 : 0 ))
