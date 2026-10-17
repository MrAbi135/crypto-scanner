#!/usr/bin/env bash
# Ingest watch: two ingest-side alarms the hourly invariants only see an hour
# later, indirectly. Runs on the host from cron, every minute:
#
#   * * * * * cd $HOME/crypto-scanner && bash ops/soak/ingest_watch.sh >> $HOME/soak-logs/ingest_watch.log 2>&1
#
#   BACKFILL NO-PASS  a candle that arrived by backfill. It has no outbox entry
#                     (live_ingest.py emits one only for the stream candle), so
#                     it gets no pass of its own and confluence never scores it.
#                     2026-10-14 21:20 M5 x15 symbols; 2026-09-27 08:10 M5 +
#                     08:00 M15 (a deploy restarted ingest on the close).
#   INGEST GAP        the candle after the newest stored one should have closed
#                     more than 120 s ago (= 7 min on M5, 17 on M15, 62 on H1).
#                     Candles are not arriving: WS down, ingest wedged, writes
#                     failing. The 10-14 outage: fires at 21:28, clears at 21:31.
#
# Read-only on the database. Writes only its own files under $INGEST_WATCH_DIR
# (default ~/soak-logs): ingest_watch.alerts (one line per finding -- not
# alerts.log, where one FIRED line per run hid four H2 rows on 2026-10-15) and
# ingest_watch.seen (what has been reported). Check J is not touched: J keeps
# firing on the late writes a backfill causes; these lines say why, earlier.
#
# A reporter, not a gate: exit 0 always. A run that cannot read its scope or
# whose query fails writes a line saying so -- never a silent clean run.
#
# Design and the cost measurement (4-5 ms + 8-9 ms per run, read-only EXPLAIN
# on the VM 2026-10-17): deploy-baseline/proposals/ingest_watch_design.md.

GAP_GRACE_S=120          # after the missing candle's close
REPEAT_S=1800            # a persisting gap / failure is repeated every 30 min
BACKFILL_KEEP_S=172800   # backfill keys are pruned after 2 days

tf_step() {
  case "$1" in
    M5) echo 300 ;; M15) echo 900 ;; H1) echo 3600 ;; H4) echo 14400 ;; D1) echo 86400 ;;
    *) return 1 ;;
  esac
}

stamp() { date -u -d "@$1" +%Y-%m-%dT%H:%MZ; }

# --- seen state: lines "key<TAB>epoch_last_reported" in $SEEN (an assoc array
#     while running, written back once at the end, atomically) ----------------
declare -A SEEN_AT=()

seen_load() {
  SEEN_AT=()
  local k e
  [ -f "$SEEN" ] || return 0
  while IFS=$'\t' read -r k e; do
    [ -n "$k" ] && [[ "$e" =~ ^[0-9]+$ ]] && SEEN_AT["$k"]="$e"
  done < "$SEEN"
}

seen_save() {
  local tmp="$SEEN.tmp.$$" k
  : > "$tmp" || return 1
  for k in "${!SEEN_AT[@]}"; do printf '%s\t%s\n' "$k" "${SEEN_AT[$k]}" >> "$tmp"; done
  mv -f "$tmp" "$SEEN"
}

# due <key> <now> <repeat_s>: true if never reported, or reported >= repeat_s ago.
# repeat_s 0 = report once only.
due() {
  local last="${SEEN_AT[$1]:-}"
  [ -z "$last" ] && return 0
  [ "$3" -gt 0 ] && [ $(( $2 - last )) -ge "$3" ]
}

prune_backfill() {  # prune_backfill <now>
  local k
  for k in "${!SEEN_AT[@]}"; do
    case "$k" in
      backfill\|*) [ $(( $1 - ${SEEN_AT[$k]} )) -gt "$BACKFILL_KEEP_S" ] && unset 'SEEN_AT[$k]' ;;
    esac
  done
}

# backfill_findings <now>
#   stdin: rows "tf|open|symbols|first_inserted" (the check-1 query).
#   stdout: one alert line per (tf, open) never reported before.
backfill_findings() {
  local now="$1" tf open n first key
  while IFS='|' read -r tf open n first; do
    [ -z "$tf" ] && continue
    key="backfill|$tf|$open"
    if due "$key" "$now" 0; then
      echo "$(stamp "$now") BACKFILL NO-PASS $tf open=$open symbols=$n first_inserted=$first"
      SEEN_AT["$key"]="$now"
    fi
  done
}

# gap_findings <now>
#   stdin: rows "symbol|tf|newest_open_epoch" for EVERY scanned context; an
#   empty epoch = no candle at all. The threshold is decided here, not in SQL,
#   so the one rule lives where it is tested.
#   stdout: per timeframe -- a GAP line when it starts and every REPEAT_S while
#   it lasts, one CLEARED line when it ends.
gap_findings() {
  local now="$1" sym tf newest step since tf_
  declare -A n_late=() oldest=() never=()
  while IFS='|' read -r sym tf newest; do
    [ -z "$sym" ] && continue
    if ! step=$(tf_step "$tf"); then
      echo "$(stamp "$now") INGEST WATCH UNKNOWN TIMEFRAME $tf ($sym)"; continue
    fi
    if [ -z "$newest" ]; then
      never["$tf"]=$(( ${never[$tf]:-0} + 1 ))
      n_late["$tf"]=$(( ${n_late[$tf]:-0} + 1 )); continue
    fi
    since=$(( now - newest - step ))           # since the newest stored candle's close
    if [ "$since" -gt $(( step + GAP_GRACE_S )) ]; then
      n_late["$tf"]=$(( ${n_late[$tf]:-0} + 1 ))
      if [ -z "${oldest[$tf]:-}" ] || [ "$newest" -lt "${oldest[$tf]}" ]; then oldest["$tf"]="$newest"; fi
    fi
  done

  for tf_ in M5 M15 H1 H4 D1; do
    if [ -n "${n_late[$tf_]:-}" ]; then
      if due "gap|$tf_" "$now" "$REPEAT_S"; then
        step=$(tf_step "$tf_")
        local detail=""
        [ -n "${oldest[$tf_]:-}" ] && detail=" newest_open=$(date -u -d "@${oldest[$tf_]}" +%Y-%m-%dT%H:%M) since_its_close=$(( now - ${oldest[$tf_]} - step ))s threshold=$(( step + GAP_GRACE_S ))s"
        [ -n "${never[$tf_]:-}" ] && detail="$detail no_candle_at_all=${never[$tf_]}"
        echo "$(stamp "$now") INGEST GAP $tf_ symbols=${n_late[$tf_]}$detail"
        SEEN_AT["gap|$tf_"]="$now"
      fi
    elif [ -n "${SEEN_AT[gap|$tf_]:-}" ]; then
      echo "$(stamp "$now") INGEST GAP CLEARED $tf_"
      unset 'SEEN_AT[gap|$tf_]'
    fi
  done
}

# problem_line <key> <now> <text>: a failure of the watch itself, repeated every
# REPEAT_S while it lasts; problem_ok <key> forgets it once it works again.
problem_line() {
  if due "$1" "$2" "$REPEAT_S"; then echo "$(stamp "$2") $3"; SEEN_AT["$1"]="$2"; fi
}
problem_ok() { unset 'SEEN_AT[$1]'; }

if [ "${INGEST_WATCH_LIB_ONLY:-}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# --------------------------------------------------------------------------
cd ~/crypto-scanner || exit 0
DIR="${INGEST_WATCH_DIR:-$HOME/soak-logs}"
mkdir -p "$DIR" || exit 0
ALERTS="$DIR/ingest_watch.alerts"
SEEN="$DIR/ingest_watch.seen"

# One run at a time: a slow docker exec must not let two minutes' runs
# interleave their seen-file writes.
exec 9> "$DIR/ingest_watch.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) previous run still going -- skipped"; exit 0; }

C="docker compose -f ops/compose/docker-compose.dev.yml"
PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner -qAt -F| -v ON_ERROR_STOP=1"
NOW=$(date -u +%s)
seen_load
prune_backfill "$NOW"

# The scope the RUNNING engine scans, read exactly as check_invariants.sh does.
SYMS=$($C exec -T engine python -c 'from scanner.config import get_settings; print(get_settings("ingest").ingest_symbols)' 2>/dev/null | tr -d '\r\n')

{
  if [ -z "$SYMS" ]; then
    problem_line scope "$NOW" "INGEST WATCH SCOPE UNKNOWN -- cannot read ingest_symbols from the running engine; nothing checked"
  else
    problem_ok scope

    if rows=$($PSQL -v syms="$SYMS" <<'SQL' 2>&1
SET default_transaction_read_only = on;
select timeframe, to_char(open_time at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI'),
       count(*), to_char(min(inserted_at) at time zone 'UTC', 'HH24:MI:SS')
from market.candles
where source = 'backfill'
  and open_time > now() - interval '2 days'
  and inserted_at > now() - interval '15 minutes'
  and timeframe in ('M5', 'M15', 'H1', 'H4')
  and symbol = any(string_to_array(:'syms', ','))
group by 1, 2 order by 2, 1;
SQL
    ); then
      problem_ok query-backfill
      backfill_findings "$NOW" <<< "$rows"
    else
      problem_line query-backfill "$NOW" "INGEST WATCH QUERY FAILED backfill: $(printf '%s' "$rows" | tr '\n' ' ' | cut -c1-200)"
    fi

    if rows=$($PSQL -v syms="$SYMS" <<'SQL' 2>&1
SET default_transaction_read_only = on;
select s.symbol, tf.name, extract(epoch from n.newest_open)::bigint
from unnest(string_to_array(:'syms', ',')) as s(symbol)
cross join (values ('M5'), ('M15'), ('H1'), ('H4'), ('D1')) as tf(name)
cross join lateral (select max(c.open_time) as newest_open from market.candles c
                    where c.symbol = s.symbol and c.timeframe = tf.name) n;
SQL
    ); then
      problem_ok query-gap
      gap_findings "$NOW" <<< "$rows"
    else
      problem_line query-gap "$NOW" "INGEST WATCH QUERY FAILED gap: $(printf '%s' "$rows" | tr '\n' ' ' | cut -c1-200)"
    fi
  fi
} > "$DIR/ingest_watch.run.$$"

# The block above is a brace group, not a subshell, and the finders read a
# here-string rather than a pipe -- so their SEEN_AT updates survive to the
# save below. (On the right of a pipe they would run in a subshell and every
# key would be reported again the next minute.)
if [ -s "$DIR/ingest_watch.run.$$" ]; then
  cat "$DIR/ingest_watch.run.$$" >> "$ALERTS"
  cat "$DIR/ingest_watch.run.$$"
fi
rm -f "$DIR/ingest_watch.run.$$"
seen_save
exit 0
