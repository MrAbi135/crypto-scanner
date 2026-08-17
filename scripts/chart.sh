#!/usr/bin/env bash
# Open the chart (Sprint S13a).
#
# The dev frontend is host-run by design -- ops/docker/frontend.Dockerfile is a
# staging/prod artifact only (S0.2 §6.1) -- so the chart needs the backend in
# Docker and Vite on the host. This does both, and refuses to open a chart that
# would be empty rather than leaving the viewer to guess why.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"

SYMBOL="${SYMBOL:-BTCUSDT}"
TIMEFRAME="${TIMEFRAME:-H1}"

# No --build: compose builds the image if it is missing, and rebuilding on
# every chart launch turns a two-second start into a two-minute one.
# scripts/dev-up.sh is the one that rebuilds.
echo "1/4  starting db, redis and the api..."
docker compose -f "$COMPOSE" up -d db redis api >/dev/null

echo "2/4  waiting for the api to answer..."
for attempt in $(seq 1 30); do
  if curl -fsS "http://localhost:8000/internal/health/ready" >/dev/null 2>&1; then
    break
  fi

  if [ "$attempt" -eq 30 ]; then
    echo "     the api never became ready. Logs:" >&2
    docker compose -f "$COMPOSE" logs --tail 30 api >&2
    exit 1
  fi

  sleep 2
done

echo "3/4  checking $SYMBOL $TIMEFRAME has candles..."
COUNT="$(
  curl -fsS "http://localhost:8000/api/v1/market/candles?symbol_id=${SYMBOL}&timeframe=${TIMEFRAME}&limit=400" |
    python -c "import json,sys; print(len(json.load(sys.stdin)['data']))"
)"

# A chart with no candles and a chart of a market where nothing happened look
# identical. Saying so here costs one line and saves the confusion entirely.
if [ "$COUNT" -eq 0 ]; then
  echo
  echo "  No candles stored for $SYMBOL $TIMEFRAME, so the chart would be blank."
  echo "  Fill it first:"
  echo
  echo "    scripts/cli.sh sync-symbols"
  echo "    scripts/cli.sh backfill --symbol $SYMBOL --timeframe $TIMEFRAME --start 2026-06-01"
  echo
  echo "  Then check what is ready:  scripts/cli.sh warmth"
  echo
  exit 1
fi

echo "     $COUNT candles ready."
echo "4/4  starting the chart on http://localhost:5173 (ctrl-c to stop)"
echo

cd "$ROOT/frontend"
exec pnpm dev --port 5173
