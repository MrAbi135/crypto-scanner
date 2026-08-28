#!/usr/bin/env bash
# Correctness invariants for the G1b soak. Companion to `soak_status.sh`.
#
# `soak_status.sh` asks whether the engine is alive. This asks whether it is
# right. On 2026-08-26 the first was green for five days while the BOS gate was
# latched in one direction, no up-impulse leg could be built on any symbol,
# ninety per cent of the event table was orphaned debris, and F3 scored zero on
# every setup ever recorded. None of that is a liveness question, so nothing
# asked it.
#
# Exit code is 0 only when every check is clean, so this belongs in cron.
#
# Run it after a deploy and before starting the soak clock. Four resets on
# 2026-08-26 were four defects found one at a time, days apart, each costing a
# fresh 72 hours.

set -uo pipefail

problems=0
flag() { echo "  !! $*"; problems=$((problems + 1)); }

trim() { printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; }

# What a violation row looks like: a check letter, an optional digit, a dot.
VIOLATION_ROW='^[A-Z][0-9]?\. '

ACK_FILE=ops/soak/acknowledged.txt

# ---------------------------------------------------------------------------
# The pattern and the SQL, kept in step.
# ---------------------------------------------------------------------------
# Every query in `invariants.sql` labels itself `select '<label>' as check`. If
# a label does not match `VIOLATION_ROW`, its rows are emitted and silently
# discarded here -- which is exactly what happened to H, H2 and I when the
# pattern still said `[A-G]`. Nothing failed; the suite just stopped watching
# three of its checks.
verify_check_labels() {
  local file=ops/soak/invariants.sql label missing=0

  [ -f "$file" ] || return 0

  while IFS= read -r label; do
    if ! printf '%s\n' "$label" | grep -qE "$VIOLATION_ROW"; then
      flag "check label '$label' does not match the row pattern -- its rows would be ignored"
      missing=1
    fi
  done < <(grep -oE "select '[^']+' as check" "$file" | sed "s/select '//; s/' as check//")

  return $missing
}

# ---------------------------------------------------------------------------
# Acknowledged violations: quieter, never hidden, and impossible to forget.
# ---------------------------------------------------------------------------
# Before this existed, check E fired every hour for twenty hours on one known
# defect whose fix was already merged and waiting on a soak window. Twenty
# identical "INVARIANTS FIRED" lines is not twenty warnings; it is one warning
# and nineteen reasons to stop reading -- and the twenty-first would have been
# a new defect nobody looked at.
#
# So an acknowledgement silences the *count*, never the line, and it rots
# loudly: past its date it fires again, and if it ever matches nothing it fires
# too. The second is the one that matters. A stale acknowledgement leaves the
# check permanently blind to that defect's return, and the only way back to a
# clean run is for somebody to delete the line.
triage_violations() {
  local all="$1" line i j matched acked=0 unacked=0 today
  local -a patterns=() untils=() whys=() hits=()

  if [ -f "$ACK_FILE" ]; then
    # Parsed from the right, in bash, with no field splitting at all.
    # The patterns are themselves pipe-separated violation lines, so `read`
    # with IFS='|' took the first two fields and dumped the whole rest into
    # the third: every pattern was truncated to its first field -- silently
    # widened to match anything with that prefix -- and the date variable
    # held "A1", so nothing could ever expire. Neither failure looked like
    # one; the check had simply stopped discriminating.
    # No explicit CR strip: an earlier line here did that, and removing it
    # broke no test, because `trim` runs on every extracted field and
    # `[[:space:]]` already covers a carriage return. The CRLF case is
    # still tested -- the file is edited on Windows and copied to the host
    # -- but by the code that actually handles it.
    while IFS= read -r raw; do
      case "$(trim "$raw")" in ''|'#'*) continue ;; esac

      # why = after the last pipe, until = before it, pattern = the rest.
      case "$raw" in *'|'*'|'*) ;; *) continue ;; esac

      rest_why=${raw##*|}
      head=${raw%|*}
      rest_until=${head##*|}
      pattern=${head%|*}

      patterns+=("$(trim "$pattern")")
      untils+=("$(trim "$rest_until")")
      whys+=("$(trim "$rest_why")")
      hits+=(0)
    done < "$ACK_FILE"
  fi

  today=$(date -u +%F)

  while IFS= read -r line; do
    [ -n "$line" ] || continue

    matched=-1

    for i in "${!patterns[@]}"; do
      case "$line" in
        *"${patterns[$i]}"*) matched=$i; hits[$i]=1; break ;;
      esac
    done

    if [ "$matched" -lt 0 ]; then
      echo "  $line"
      unacked=$((unacked + 1))
    elif [[ "$today" > "${untils[$matched]}" ]]; then
      # Not a grace period that renews itself. A fix that missed the date it
      # was given is news, which is the whole reason a date is required.
      echo "  $line"
      flag "acknowledgement expired ${untils[$matched]}: ${whys[$matched]}"
    else
      echo "  ~~ known (until ${untils[$matched]}): $line"
      acked=$((acked + 1))
    fi
  done <<< "$all"

  [ "$unacked" -gt 0 ] && flag "$unacked database invariant violation(s)"

  for j in "${!patterns[@]}"; do
    if [ "${hits[$j]}" -eq 0 ]; then
      # The defect is gone and the line is still here. Left alone, this check
      # is now blind to that defect coming back.
      flag "acknowledgement matched nothing -- delete it from $ACK_FILE: ${patterns[$j]}"
    fi
  done

  [ "$acked" -gt 0 ] && echo "  ($acked acknowledged; see $ACK_FILE)"

  return 0
}

# Sourced by `test_check_invariants.sh` to reach the helpers above without
# running any check. Placed here rather than at the top so the helpers are
# defined and nothing below -- which needs docker, a database and the repo --
# is even parsed for a caller that only wants to test the triage rules.
if [ "${INVARIANTS_LIB_ONLY:-}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

cd ~/crypto-scanner || exit 2
C="docker compose -f ops/compose/docker-compose.dev.yml"
PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner"

release=$(grep -h '^SCANNER_RELEASE' ops/env/dev.env | cut -d= -f2)
echo "invariants @ $(date -u +%Y-%m-%dT%H:%M:%SZ)  release=${release}"
echo

# ---------------------------------------------------------------------------
# A. Is the BOS gate still breaking in the direction it is open for?
# ---------------------------------------------------------------------------
# The check that would have caught the five-day defect. It lives here rather
# than in `invariants.sql` because deciding it needs the maintained trend,
# which is in Redis and not in any table.
#
# The naive form -- "lower lows but no BOS_DOWN" -- fires on every healthy
# series, because §3.5 breaks only *with* the trend: a bullish symbol prints
# lower lows and correctly records none. Asked that way against production it
# flagged five contexts, all five working exactly as the doctrine says.
#
# The threshold is §3.4's own rather than a number chosen here.
# `P.structure.idle_candles = 100`: a trend that goes a hundred closed candles
# without an external BOS "additionally applies" as RANGING. So a symbol still
# holding BULLISH with no BOS_UP in a hundred candles is in a state §3.4 says
# cannot persist, and exactly one of two things is wrong -- the gate is shut,
# or the idle rule failed to demote it. Neither is a quiet market: a quiet
# market would have idled to RANGING and been skipped below.
#
# A first draft used forty candles and fired on two contexts that were merely
# consolidating. Forty was a guess; a hundred is the doctrine's.

echo "-- A. gate open but not breaking --"

SHIFT_ALGO="s6-structure-shift-v2"
IDLE_CANDLES=100          # P.structure.idle_candles, SLS §3.4

tf_seconds() {
  case "$1" in
    M5)  echo 300 ;;
    M15) echo 900 ;;
    H1)  echo 3600 ;;
    H4)  echo 14400 ;;
    *)   echo 3600 ;;
  esac
}

keys=$($C exec -T redis redis-cli --scan --pattern "scanner:engine-state:shift:${SHIFT_ALGO}:*" 2>/dev/null | tr -d '\r' | sort)

for key in $keys; do
  raw=$($C exec -T redis redis-cli GET "$key" 2>/dev/null | tr -d '\r')
  [ -z "$raw" ] && continue

  timeframe=${key##*:}
  symbol=${key%:*}
  symbol=${symbol##*:}
  trend=$(echo "$raw" | grep -oE '"trend_state":"[A-Z_]+"' | cut -d'"' -f4)

  # Only the two states §3.4 draws the idle edge out of. RANGING opens no gate
  # at all, and the CAUTION states are mid-transition -- §3.4 will not idle out
  # of one either, so neither has an assertion to make here.
  case "$trend" in
    BULLISH) want=BOS_UP ;;
    BEARISH) want=BOS_DOWN ;;
    *)
      printf '%-8s %-4s trend=%-8s (no gate direction, skipped)\n' "$symbol" "$timeframe" "$trend"
      continue
      ;;
  esac

  row=$($PSQL -At -F' ' -c "
    select
      (select count(*) from detection.engine_events
        where symbol='${symbol}' and timeframe='${timeframe}'
          and event_type like 'STRUCTURE_EXTERNAL_%'),
      coalesce((select extract(epoch from max(event_at))::bigint
                  from detection.engine_events
                 where symbol='${symbol}' and timeframe='${timeframe}'
                   and event_type='${want}'), 0),
      coalesce((select extract(epoch from max(open_time))::bigint
                  from market.candles
                 where symbol='${symbol}' and timeframe='${timeframe}'), 0)" 2>/dev/null | tr -d '\r')

  read -r labels last_break newest <<<"$row"

  # A series still warming up has nothing to say.
  [ "${labels:-0}" -lt 5 ] && continue

  step=$(tf_seconds "$timeframe")

  if [ "${last_break:-0}" -eq 0 ]; then
    since="never"
    candles=999999
  else
    candles=$(( (${newest:-0} - last_break) / step ))
    since=$(date -u -d "@${last_break}" +%Y-%m-%dT%H:%MZ)
  fi

  printf '%-8s %-4s trend=%-8s last %-8s %-18s %s candles ago\n' \
    "$symbol" "$timeframe" "$trend" "$want" "$since" "$candles"

  if [ "$candles" -gt "$IDLE_CANDLES" ]; then
    if [ "${last_break:-0}" -eq 0 ]; then
      flag "$symbol $timeframe holds $trend and has never recorded a $want"
    else
      flag "$symbol $timeframe holds $trend with no $want in $candles candles (§3.4 idles at $IDLE_CANDLES)"
    fi
  fi
done
echo

# ---------------------------------------------------------------------------
# B-G. Everything answerable from the database alone.
# ---------------------------------------------------------------------------
# Each query in `invariants.sql` emits one row per violation and nothing when
# satisfied, so the presence of a row is the verdict. Violations are matched on
# their check letter rather than counted from the raw output: psql prints
# banners of its own, and a banner read as data is exactly how a watcher
# earlier the same day reported a result it had not found.

# Named for the range it covered when it was written; it runs whatever the SQL
# file contains.
echo "-- database invariants --"

verify_check_labels

out=$($PSQL -At -F'|' -v ON_ERROR_STOP=1 < ops/soak/invariants.sql 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "$out" | tail -5 | sed 's/^/  /'
  flag "invariants.sql failed to run (exit $rc) -- the checks did not happen"
else
  # `[A-Z]` and an optional digit, not `[A-G]`.
  #
  # The pattern was written when the file ended at G, and H, H2 and I were
  # added after it. Their rows were emitted by psql and dropped on the floor
  # here: a violation nobody counted, printed nowhere, exiting zero. None of
  # them happened to be firing, so the suite looked clean and was blind --
  # which is this file's own recurring defect, in its own runner.
  #
  # `verify_check_labels` below keeps the pattern and the SQL in step, so the
  # next letter cannot go missing the same way.
  violations=$(echo "$out" | grep -E "$VIOLATION_ROW" || true)

  if [ -n "$violations" ]; then
    triage_violations "$violations"
  else
    echo "  clean"
  fi
fi
echo

# ---------------------------------------------------------------------------
# H. The impulse-leg ratchet, which no query can see.
# ---------------------------------------------------------------------------
# Legs are recomputed per pass and never persisted, so this one runs the domain
# code over the same candles the engine reads. From the engine *image*, because
# the running container's rootfs is read-only.

echo "-- H. impulse legs in both directions --"

if [ ! -f ops/soak/leg_invariant.py ]; then
  flag "ops/soak/leg_invariant.py is missing -- the ratchet check did not run"
else
  net=$(docker inspect scanner-dev-engine-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null)

  legs=$(docker run --rm --network "$net" --env-file ops/env/dev.env \
           --entrypoint python -v "$PWD/ops/soak/leg_invariant.py:/tmp/leg.py:ro" \
           -w /app scanner-dev-engine /tmp/leg.py 2>&1)
  lrc=$?

  echo "$legs" | sed 's/^/  /'

  if [ "$lrc" -ne 0 ]; then
    flag "leg_invariant.py failed (exit $lrc) -- the ratchet check did not run"
  elif echo "$legs" | grep -q '^VIOLATION'; then
    flag "impulse legs are one-directional on at least one context (see above)"
  fi
fi
echo

if [ "$problems" -eq 0 ]; then
  echo "OK -- all invariants clean"
else
  echo "$problems problem(s)"
fi

exit $(( problems > 0 ? 1 : 0 ))
