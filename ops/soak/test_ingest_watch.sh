#!/usr/bin/env bash
# Tests for ingest_watch.sh's decisions at simulated times (runs in CI via the
# shell job). The queries need the VM; what is tested here is everything that
# decides whether a line is written.
set -uo pipefail
cd "$(dirname "$0")" || exit 2
INGEST_WATCH_LIB_ONLY=1 . ./ingest_watch.sh

failures=0
OUT=$(mktemp)
SEEN=$(mktemp)
trap 'rm -f "$OUT" "$SEEN"' EXIT

at() { date -u -d "2026-10-14 $1" +%s; }
ep() { date -u -d "2026-10-14 $1" +%s; }      # an open time as epoch

# The finders must run in THIS shell (their SEEN_AT updates are the state under
# test), so output goes to a file, never through $( ).
gap() { gap_findings "$(at "$1")" <<< "$2" > "$OUT"; }
bf()  { backfill_findings "$(at "$1")" <<< "$2" > "$OUT"; }

check() {  # check <label> <expected substring | "" for no output>
  local got; got=$(cat "$OUT")
  if { [ -z "$2" ] && [ -z "$got" ]; } || { [ -n "$2" ] && [[ "$got" == *"$2"* ]]; }; then
    echo "  ok    $1${got:+ -> $(echo "$got" | head -1)}"
  else
    echo "  FAIL  $1"; echo "        want: ${2:-<nothing>}"; echo "        got:  ${got:-<nothing>}"
    failures=$((failures + 1))
  fi
}
lines() {  # lines <label> <n>
  local n; n=$(grep -c . "$OUT")
  if [ "$n" = "$2" ]; then echo "  ok    $1 ($n lines)"; else echo "  FAIL  $1: $n lines, want $2"; failures=$((failures + 1)); fi
}

M5_2115="BTCUSDT|M5|$(ep 21:15)"

echo "INGEST GAP threshold (M5: the 21:20 candle closes 21:25; fires 120 s after)"
SEEN_AT=()
gap "21:26:59" "$M5_2115"; check "119 s after the missing close -> nothing" ""
gap "21:27:00" "$M5_2115"; check "exactly 120 s -> nothing (more than, not equal)" ""
gap "21:27:01" "$M5_2115"; check "121 s -> GAP" "INGEST GAP M5 symbols=1 newest_open=2026-10-14T21:15 since_its_close=421s threshold=420s"

echo
echo "repeat and clear"
SEEN_AT=()
gap "21:28:02" "$M5_2115"; check "first sight -> GAP" "INGEST GAP M5"
gap "21:29:02" "$M5_2115"; check "next minute, still gapped -> nothing" ""
gap "21:58:01" "$M5_2115"; check "29 min 59 s later -> nothing" ""
gap "21:58:02" "$M5_2115"; check "30 min later -> GAP again" "INGEST GAP M5"
gap "21:59:02" "BTCUSDT|M5|$(ep 21:50)"; check "data back -> CLEARED" "INGEST GAP CLEARED M5"
gap "22:00:02" "BTCUSDT|M5|$(ep 21:50)"; check "after CLEARED -> nothing" ""

echo
echo "grouping and odd rows"
SEEN_AT=()
gap "21:28:02" "$(printf 'BTCUSDT|M5|%s\nETHUSDT|M5|%s\nXRPUSDT|M5|%s\nBTCUSDT|H1|%s' \
  "$(ep 21:15)" "$(ep 21:10)" "$(ep 21:20)" "$(ep 20:00)")"
check "two late M5 symbols -> one line, oldest newest_open" "INGEST GAP M5 symbols=2 newest_open=2026-10-14T21:10"
lines "on-time XRP M5 and on-time H1 add nothing" 1
SEEN_AT=()
gap "21:28:02" "NEWUSDT|D1|"; check "a context with no candle at all -> GAP" "INGEST GAP D1 symbols=1 no_candle_at_all=1"
SEEN_AT=()
gap "21:28:02" "BTCUSDT|W1|$(ep 00:00)"; check "unknown timeframe is reported, not skipped" "INGEST WATCH UNKNOWN TIMEFRAME W1"

echo
echo "BACKFILL NO-PASS"
SEEN_AT=()
bf "21:31:02" "M5|2026-10-14T21:20|15|21:30:00"
check "one backfill row -> one line" "BACKFILL NO-PASS M5 open=2026-10-14T21:20 symbols=15 first_inserted=21:30:00"
bf "21:32:02" "M5|2026-10-14T21:20|15|21:30:00"; check "same key next minute -> nothing" ""
bf "21:44:02" "$(printf 'M5|2026-10-14T21:20|15|21:30:00\nM15|2026-10-14T21:15|15|21:30:01')"
check "a new key beside a seen one -> only the new one" "BACKFILL NO-PASS M15 open=2026-10-14T21:15"
lines "...and exactly one line" 1
bf "21:45:02" ""; check "no rows -> nothing" ""
if grep -q "timeframe in ('M5', 'M15', 'H1', 'H4')" ingest_watch.sh; then
  echo "  ok    the backfill query leaves D1 bulk loads out"
else
  echo "  FAIL  the backfill query no longer limits timeframes"; failures=$((failures + 1))
fi

echo
echo "the 2026-10-14 outage, one run a minute (WS down 21:21:07-21:26:58, M5 21:20 backfilled 21:30:00)"
SEEN_AT=(); : > "$OUT.all"
for m in 24 25 26 27 28 29; do gap "21:$m:02" "$M5_2115"; cat "$OUT" >> "$OUT.all"; done
gap "21:30:02" "BTCUSDT|M5|$(ep 21:25)"; cat "$OUT" >> "$OUT.all"
bf  "21:30:02" "M5|2026-10-14T21:20|15|21:30:00"; cat "$OUT" >> "$OUT.all"
gap "21:31:02" "BTCUSDT|M5|$(ep 21:25)"; cat "$OUT" >> "$OUT.all"
bf  "21:31:02" "M5|2026-10-14T21:20|15|21:30:00"; cat "$OUT" >> "$OUT.all"
mv "$OUT.all" "$OUT"
lines "the whole episode is three lines" 3
check "GAP at 21:27" "2026-10-14T21:27Z INGEST GAP M5"
check "CLEARED at 21:30" "2026-10-14T21:30Z INGEST GAP CLEARED M5"
check "BACKFILL NO-PASS at 21:30, once" "2026-10-14T21:30Z BACKFILL NO-PASS M5 open=2026-10-14T21:20"

echo
echo "seen state on disk"
SEEN_AT=([gap\|M5]=100 [backfill\|M5\|2026-10-14T21:20]=200 [scope]=300)
seen_save; SEEN_AT=(); seen_load
if [ "${SEEN_AT[gap|M5]:-}" = 100 ] && [ "${SEEN_AT[backfill|M5|2026-10-14T21:20]:-}" = 200 ] && [ "${#SEEN_AT[@]}" = 3 ]; then
  echo "  ok    save then load keeps every key"
else
  echo "  FAIL  save/load lost keys: ${!SEEN_AT[*]}"; failures=$((failures + 1))
fi
prune_backfill $(( 200 + 172801 ))
if [ -z "${SEEN_AT[backfill|M5|2026-10-14T21:20]:-}" ] && [ "${SEEN_AT[gap|M5]:-}" = 100 ]; then
  echo "  ok    a backfill key older than 2 days is pruned, a gap key is not"
else
  echo "  FAIL  prune: ${!SEEN_AT[*]}"; failures=$((failures + 1))
fi

echo
echo "the watch's own failures"
SEEN_AT=()
problem_line scope "$(at 21:00:00)" "INGEST WATCH SCOPE UNKNOWN" > "$OUT"; check "first failure -> a line" "INGEST WATCH SCOPE UNKNOWN"
problem_line scope "$(at 21:01:00)" "INGEST WATCH SCOPE UNKNOWN" > "$OUT"; check "still failing next minute -> nothing" ""
problem_line scope "$(at 21:30:00)" "INGEST WATCH SCOPE UNKNOWN" > "$OUT"; check "still failing 30 min later -> a line" "SCOPE UNKNOWN"
problem_ok scope
problem_line scope "$(at 21:31:00)" "INGEST WATCH SCOPE UNKNOWN" > "$OUT"; check "fails again after working -> a line at once" "SCOPE UNKNOWN"

echo
if [ "$failures" -eq 0 ]; then echo "OK -- ingest watch holds"; else echo "$failures failure(s)"; fi
exit $(( failures > 0 ? 1 : 0 ))
