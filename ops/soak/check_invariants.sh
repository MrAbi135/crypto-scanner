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

cd ~/crypto-scanner || exit 2
C="docker compose -f ops/compose/docker-compose.dev.yml"
PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner"

problems=0
flag() { echo "  !! $*"; problems=$((problems + 1)); }

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

echo "-- B-G. database invariants --"

out=$($PSQL -At -F'|' -v ON_ERROR_STOP=1 < ops/soak/invariants.sql 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "$out" | tail -5 | sed 's/^/  /'
  flag "invariants.sql failed to run (exit $rc) -- the checks did not happen"
else
  violations=$(echo "$out" | grep -E '^[A-G]\. ' || true)

  if [ -n "$violations" ]; then
    echo "$violations" | sed 's/^/  /'
    n=$(echo "$violations" | grep -c .)
    flag "$n database invariant violation(s)"
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
