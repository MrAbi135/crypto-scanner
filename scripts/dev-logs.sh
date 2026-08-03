#!/usr/bin/env bash
# Tail structured logs. Optional service name(s) (S0.2 §10).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"
docker compose -f "$COMPOSE" logs -f "$@"
