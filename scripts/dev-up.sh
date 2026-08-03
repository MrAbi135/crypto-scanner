#!/usr/bin/env bash
# Bring up the dev stack and wait for health (S0.2 §10).
# Pass compose flags through, e.g. `scripts/dev-up.sh --profile obs`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"

docker compose -f "$COMPOSE" "$@" up -d --build

echo "waiting for services to become healthy (up to 90s)..."
for _ in $(seq 1 18); do
  if ! docker compose -f "$COMPOSE" ps --format '{{.Service}} {{.Health}}' | grep -Eq 'starting|unhealthy'; then
    break
  fi
  sleep 5
done

docker compose -f "$COMPOSE" ps
