#!/usr/bin/env bash
# Sprint S0.2 verification harness (S0.2 §16). Automates checks 1-9 and records
# evidence to docs/evidence/S0/. Checks 10-16 are manual G0 evidence (see
# docs/setup.md#verification). Any failure stops the run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVID="$ROOT/docs/evidence/S0"
mkdir -p "$EVID"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"
log() { printf '\n=== %s ===\n' "$*"; }

# pnpm without requiring an admin-installed shim: prefer a real pnpm on PATH
# (WSL2 / CI corepack-enable), else fall back to `corepack pnpm` (no shim needed).
PNPM="pnpm"; command -v pnpm >/dev/null 2>&1 || PNPM="corepack pnpm"

cd "$ROOT/backend"
log "1/9 ruff";        uv run ruff check src tests | tee "$EVID/${STAMP}-1-ruff.txt"
                       uv run ruff format --check src tests | tee -a "$EVID/${STAMP}-1-ruff.txt"
log "2/9 mypy";        uv run mypy | tee "$EVID/${STAMP}-2-mypy.txt"
log "3/9 imports";     uv run lint-imports | tee "$EVID/${STAMP}-3-imports.txt"
log "4/9 pytest";      uv run pytest -m "not integration" | tee "$EVID/${STAMP}-4-pytest.txt"

log "5/9 frontend";    ( cd "$ROOT/frontend" && $PNPM lint && $PNPM typecheck && $PNPM test && $PNPM build ) \
                         | tee "$EVID/${STAMP}-5-frontend.txt"

log "6/9 compose config"
docker compose -f "$COMPOSE" config -q && echo "dev compose valid" | tee "$EVID/${STAMP}-6-compose.txt"
docker compose -f "$ROOT/ops/compose/docker-compose.staging.yml" config -q && echo "staging compose valid" \
  | tee -a "$EVID/${STAMP}-6-compose.txt"

log "7/9 dev-up";      "$ROOT/scripts/dev-up.sh" | tee "$EVID/${STAMP}-7-devup.txt"

log "8/9 health"
# api publishes :8000 on the host; ingest/engine/worker are internal-only, so
# probe them via `docker compose exec` (topology: only api is host-published).
{
  echo "api (host :8000):"
  curl -fsS "http://localhost:8000/internal/health/live"; echo " live:8000 ok"
  curl -fsS "http://localhost:8000/internal/health/ready"; echo " ready:8000 ok"
  for entry in "ingest:8001" "engine:8002" "worker:8003"; do
    svc="${entry%%:*}"; port="${entry##*:}"
    echo "${svc} (internal :${port}):"
    docker compose -f "$COMPOSE" exec -T "$svc" python -c \
      "import urllib.request; r=urllib.request.urlopen('http://localhost:${port}/internal/health/ready',timeout=3); print(r.status, r.read().decode())"
  done
  echo "metrics (api :8000):"
  curl -fsS "http://localhost:8000/internal/metrics" | head -5
} | tee "$EVID/${STAMP}-8-health.txt"

log "9/9 alembic baseline (idempotent)"
# Run in a one-off api container on the compose network so it reaches db:5432
# (the container inherits dev.env's DSN; a host-run alembic can't resolve `db`).
docker compose -f "$COMPOSE" run --rm api alembic upgrade head | tee "$EVID/${STAMP}-9-alembic.txt"
docker compose -f "$COMPOSE" run --rm api alembic upgrade head | tee -a "$EVID/${STAMP}-9-alembic.txt"

log "AUTOMATED S0.2 CHECKS PASSED (${STAMP})"
echo "Manual G0 evidence (10-16): boot-refusal, /ready 503 on db down, edge 403,"
echo "cold rebuild clock, CI PR block, staging local run, Grafana heartbeats —"
echo "see docs/setup.md#verification."
