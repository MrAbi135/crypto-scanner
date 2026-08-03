#!/usr/bin/env bash
# Stop the dev stack. `--wipe` also removes volumes (guarded) (S0.2 §10).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"

if [ "${1:-}" = "--wipe" ]; then
  read -r -p "This DELETES db + redis volumes. Type 'wipe' to confirm: " answer
  [ "$answer" = "wipe" ] || { echo "aborted"; exit 1; }
  docker compose -f "$COMPOSE" down -v
else
  docker compose -f "$COMPOSE" down
fi
