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
# Which acknowledgements matched something, across EVERY triage call in the
# run. It has to be global: routing check H through triage (PR #213) made this
# function run twice, and a per-call sweep then reported the leg
# acknowledgement as "matched nothing" during the SQL pass and as `~~ known`
# during the leg pass -- the same line, both praised and condemned, in one run.
declare -A ACK_HITS=()

triage_violations() {
  # `noun` because this is no longer only the SQL checks: check H routes
  # through here too, and calling a leg asymmetry a "database invariant
  # violation" would send the next reader to the wrong file.
  local all="$1" noun="${2:-database invariant}"
  local line i j matched acked=0 unacked=0 today
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

      ACK_HITS["$(trim "$pattern")"]=${ACK_HITS["$(trim "$pattern")"]:-0}
    done < "$ACK_FILE"
  fi

  today=$(date -u +%F)

  while IFS= read -r line; do
    [ -n "$line" ] || continue

    matched=-1

    for i in "${!patterns[@]}"; do
      case "$line" in
        *"${patterns[$i]}"*) matched=$i; hits[$i]=1; ACK_HITS["${patterns[$i]}"]=1; break ;;
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

  [ "$unacked" -gt 0 ] && flag "$unacked $noun violation(s)"

  [ "$acked" -gt 0 ] && echo "  ($acked acknowledged; see $ACK_FILE)"

  return 0
}

report_stale_acknowledgements() {
  # The defect is gone and the line is still here. Left alone, the suite is
  # blind to that defect coming back.
  #
  # The patterns are re-read FROM THE FILE, not taken from whatever a triage
  # call happened to register. `triage_violations` only runs when a check has
  # violations, so a suite where everything is clean never populated the hit
  # map at all -- and this sweep, the one guard whose entire purpose is the
  # clean run with a stale line in it, iterated an empty map and said nothing.
  # It was a check that could not fail, inside the alarm written to prevent
  # exactly that. Caught on 2026-09-10, when BTCUSDT H1's impulse asymmetry
  # resolved on its own and the acknowledgement for it sat unmatched through a
  # clean run without a word.
  [ -f "$ACK_FILE" ] || return 0

  local raw head pattern
  while IFS= read -r raw; do
    case "$(trim "$raw")" in ''|'#'*) continue ;; esac
    case "$raw" in *'|'*'|'*) ;; *) continue ;; esac

    head=${raw%|*}
    pattern=$(trim "${head%|*}")

    if [ "${ACK_HITS[$pattern]:-0}" -eq 0 ]; then
      flag "acknowledgement matched nothing -- delete it from $ACK_FILE: $pattern"
    fi
  done < "$ACK_FILE"
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

# The version the RUNNING engine writes, read off the running engine -- not a
# literal here, and not the checked-out tree either. A literal goes stale the
# moment the shift engine's version bumps, and a stale pattern matches no Redis
# key at all: the loop below would scan nothing, find nothing, and report
# clean. That is the exact failure this file exists to refuse, so the pattern
# is taken from the artifact under test and its absence is itself a problem.
# The symbols the engine actually scans, read off the RUNNING engine for the
# same reason the shift version is: a list written down here goes stale, and a
# stale scope is a suite that checks the wrong contexts in silence. Everything
# else in these tables has candles without having been scanned -- golden
# fixtures, symbols backfilled ahead of a deploy -- and every check below
# reads that absence as a defect.
INGEST_SYMBOLS=$($C exec -T engine printenv SCANNER_INGEST_SYMBOLS 2>/dev/null | tr -d '' | tr -d '
')

if [ -z "$INGEST_SYMBOLS" ]; then
  flag "cannot read SCANNER_INGEST_SYMBOLS from the running engine -- scope unknown"
  INGEST_SYMBOLS="__none__"
fi

echo "-- scope: $INGEST_SYMBOLS"
echo

SHIFT_ALGO=$($C exec -T engine grep -oE   'STRUCTURE_SHIFT_ALGO_VERSION = "[^"]+"'   /app/src/scanner/application/detection/structure_shift_replay.py 2>/dev/null   | cut -d'"' -f2 | tr -d '
')

if [ -z "$SHIFT_ALGO" ]; then
  flag "cannot read STRUCTURE_SHIFT_ALGO_VERSION from the running engine -- check A did not run"
  SHIFT_ALGO="__unreadable__"
fi
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

if [ -z "$keys" ] && [ "$SHIFT_ALGO" != "__unreadable__" ]; then
  flag "no shift-engine state matched ${SHIFT_ALGO} -- check A scanned nothing"
fi

for key in $keys; do
  raw=$($C exec -T redis redis-cli GET "$key" 2>/dev/null | tr -d '\r')
  [ -z "$raw" ] && continue

  timeframe=${key##*:}
  symbol=${key%:*}
  symbol=${symbol##*:}

  # Only the scanned universe. Shift state lingers for anything ever replayed
  # -- golden fixtures among them -- and a context nobody scans cannot be
  # behind on anything.
  case ",$INGEST_SYMBOLS," in *",$symbol,"*) ;; *) continue ;; esac
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
    with bracket as (
      select
        (select (payload::json->>'price')::numeric
           from detection.engine_events
          where symbol='${symbol}' and timeframe='${timeframe}'
            and event_type='SWING_EXTERNAL_HIGH'
          order by event_at desc limit 1) as hi,
        (select max(event_at)
           from detection.engine_events
          where symbol='${symbol}' and timeframe='${timeframe}'
            and event_type='SWING_EXTERNAL_HIGH') as hi_at,
        (select (payload::json->>'price')::numeric
           from detection.engine_events
          where symbol='${symbol}' and timeframe='${timeframe}'
            and event_type='SWING_EXTERNAL_LOW'
          order by event_at desc limit 1) as lo,
        (select max(event_at)
           from detection.engine_events
          where symbol='${symbol}' and timeframe='${timeframe}'
            and event_type='SWING_EXTERNAL_LOW') as lo_at
    ),
    recent as (
      select close, open_time from market.candles
       where symbol='${symbol}' and timeframe='${timeframe}'
       order by open_time desc limit ${IDLE_CANDLES}
    )
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
                 where symbol='${symbol}' and timeframe='${timeframe}'), 0),
      (select case
         when hi is null or lo is null then 'no-bracket'
         when (select count(*) from recent where close > hi and open_time > hi_at) > 0
          and (select count(*) from recent where close < lo and open_time > lo_at) > 0 then 'both'
         when (select count(*) from recent where close > hi and open_time > hi_at) > 0 then 'above'
         when (select count(*) from recent where close < lo and open_time > lo_at) > 0 then 'below'
         when (select count(*) from recent where close > hi or close < lo) > 0 then 'left-earlier'
         else 'inside'
       end from bracket)" 2>/dev/null | tr -d '\r')

  read -r labels last_break newest bracket <<<"$row"

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

  printf '%-8s %-4s trend=%-8s last %-8s %-18s %s candles ago, closes %s
'     "$symbol" "$timeframe" "$trend" "$want" "$since" "$candles" "${bracket:-?}"

  # A break test must read only closes made AFTER the level it is compared
  # against existed. The first draft compared the CURRENT bracket against ALL
  # of the last 100 closes, and on 2026-09-10 that fired on BTCUSDT M15: the
  # market had topped at 79,760 and walked its external highs down to 78,054,
  # so day-old closes sat far above a bracket built an hour ago. No break was
  # missing -- there was nothing left to break, which the seven CHOCH_DOWNs in
  # the same window say plainly. Same shape as the window-local index trap:
  # one side of the comparison was "now" and the other was "the last hundred
  # candles", and nothing said so.
  #
  # `left-earlier` is that case, named rather than silently folded into
  # `inside`: price did leave the bracket, but before the bracket existed.
  #
  # SLS 3.4's idle rule has TWO conditions -- no external break AND every
  # close inside the current external bracket -- and only the first was asked
  # here. A BULLISH context whose price has fallen out of its bracket meets
  # the first and fails the second, so the doctrine keeps its trend and the
  # engine is right to hold it. This check called that a defect on ETHUSDT H4
  # for nine days: 114 candles with no BOS_UP, against a bracket floor of
  # 2431.61 that the closes had dropped to 2381.88 below.
  #
  # Two things still deserve the flag, and they are opposite failures:
  #
  #   inside -- both idle conditions hold, so 3.4 says RANGING while the state
  #             says otherwise: the idle rule did not fire.
  #   above (BULLISH) / below (BEARISH) -- price closed clean through its own
  #             bracket in the direction the gate is open for and no break was
  #             recorded. That is the shut-gate defect this check was written
  #             for, and the one that cost five days.
  #
  # Leaving the bracket on the counter-trend side is neither: 3.5 records
  # breaks only *with* the trend, so there is no missing event to go find.
  if [ "$candles" -gt "$IDLE_CANDLES" ]; then
    case "${trend}:${bracket}" in
      *:inside)
        why="every close sits inside its external bracket, so 3.4 should have idled it to RANGING" ;;
      *:left-earlier)
        why="" ;;   # left the bracket before the bracket existed -- see above
      BULLISH:above|BULLISH:both|BEARISH:below|BEARISH:both)
        why="price closed through its own bracket in the trend's direction and no break was recorded" ;;
      *)
        why="" ;;
    esac

    if [ -n "$why" ]; then
      if [ "${last_break:-0}" -eq 0 ]; then
        flag "$symbol $timeframe holds $trend and has never recorded a $want -- $why"
      else
        flag "$symbol $timeframe holds $trend with no $want in $candles candles -- $why"
      fi
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

out=$($PSQL -At -F'|' -v ON_ERROR_STOP=1 -v syms="$INGEST_SYMBOLS" < ops/soak/invariants.sql 2>&1)
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

  # Everything except the VIOLATION rows: those go through triage below, and
  # printing them twice would put an un-triaged copy above the `~~ known` one.
  echo "$legs" | grep -v '^VIOLATION' | sed 's/^/  /'

  leg_violations=$(echo "$legs" | grep '^VIOLATION' || true)

  if [ "$lrc" -ne 0 ]; then
    flag "leg_invariant.py failed (exit $lrc) -- the ratchet check did not run"
  elif [ -n "$leg_violations" ]; then
    # Triaged like every SQL check, rather than flagged straight.
    #
    # This check used to call `flag` on any VIOLATION line, which put a whole
    # class of violation beyond the reach of acknowledged.txt: H could never
    # be acknowledged, so a known and investigated leg asymmetry blocked every
    # deploy until the engine itself changed. That is not a stricter policy,
    # it is a gap -- the machinery exists precisely so a violation can be held
    # visibly, with an expiry, instead of being bypassed with --force.
    triage_violations "$leg_violations" "impulse-leg"
  fi
fi
echo

report_stale_acknowledgements

if [ "$problems" -eq 0 ]; then
  echo "OK -- all invariants clean"
else
  echo "$problems problem(s)"
fi

exit $(( problems > 0 ? 1 : 0 ))
