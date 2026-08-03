#!/usr/bin/env bash
# "Does everything still assemble" check (S0.2 §10): backend images + FE build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker compose -f "$ROOT/ops/compose/docker-compose.dev.yml" build
cd "$ROOT/frontend"
pnpm build
