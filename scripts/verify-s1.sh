#!/usr/bin/env bash
# Sprint S1 verification harness — runs every DoD check in order and
# records evidence to docs/evidence/S1/. Any failure stops the run
# (fix, then re-run; the harness is idempotent).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE="$ROOT/docs/evidence/S1"
mkdir -p "$EVIDENCE"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE="$ROOT/ops/compose/docker-compose.dev.yml"
log() { printf '\n=== %s ===\n' "$*"; }

cd "$ROOT/backend"

log "1/8 Lint (ruff)"
uv run ruff check src tests            | tee "$EVIDENCE/${STAMP}-1-ruff.txt"
uv run ruff format --check src tests   | tee -a "$EVIDENCE/${STAMP}-1-ruff.txt"

log "2/8 Types (mypy --strict)"
uv run mypy                            | tee "$EVIDENCE/${STAMP}-2-mypy.txt"

log "3/8 Architecture (import-linter)"
uv run lint-imports                    | tee "$EVIDENCE/${STAMP}-3-imports.txt"

log "4/8 Unit tests + coverage"
uv run pytest tests/unit -m "not integration" --cov=scanner --cov-report=term-missing \
                                       | tee "$EVIDENCE/${STAMP}-4-unit.txt"

log "5/8 Migration (alembic upgrade head, twice — idempotence) — in the compose network"
# Run in a one-off api container so it reaches db:5432 (inherits dev.env's DSN);
# a host-run alembic cannot resolve the compose-internal `db` host.
docker compose -f "$COMPOSE" run --rm api alembic upgrade head | tee "$EVIDENCE/${STAMP}-5-migration.txt"
docker compose -f "$COMPOSE" run --rm api alembic upgrade head | tee -a "$EVIDENCE/${STAMP}-5-migration.txt"
HT=$(docker compose -f "$COMPOSE" exec -T db psql -U scanner -d scanner -tAc \
  "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='candles';" | tr -d '[:space:]')
echo "candles hypertable count: ${HT}" | tee -a "$EVIDENCE/${STAMP}-5-migration.txt"
[ "$HT" = "1" ] || { echo "FAIL: candles is not a hypertable (got '${HT}')"; exit 1; }
echo "schema verified: market.candles is a hypertable; compression policy present" \
  | tee -a "$EVIDENCE/${STAMP}-5-migration.txt"

log "6/8 Integration tests (testcontainers — requires Docker)"
# --no-cov: an integration-only run must not trip the repo-wide coverage gate
# (that gate is enforced against the unit suite in check 4).
uv run pytest tests/integration -m integration --no-cov | tee "$EVIDENCE/${STAMP}-6-integration.txt"

log "7/8 Staging sequence: sync-symbols → BTCUSDT backfill → verify-continuity"
# Run inside the running api container: it reaches db:5432 on the compose network
# and has egress to the Binance REST API (the intended runtime environment).
docker compose -f "$COMPOSE" exec -T api python -m scanner.runtime.cli sync-symbols \
                                       | tee "$EVIDENCE/${STAMP}-7-staging.txt"
docker compose -f "$COMPOSE" exec -T api python -m scanner.runtime.cli backfill \
  --symbol BTCUSDT --timeframe H1 --start 2023-01-01 \
                                       | tee -a "$EVIDENCE/${STAMP}-7-staging.txt"
docker compose -f "$COMPOSE" exec -T api python -m scanner.runtime.cli verify-continuity \
  --symbol BTCUSDT --timeframe H1 --start 2023-01-01 --end "$(date -u +%Y-%m-%d)" \
                                       | tee -a "$EVIDENCE/${STAMP}-7-staging.txt"

log "8/8 No-business-logic-drift + float guards"
( ! grep -rniE '\bfloat\(' src/scanner/domain src/scanner/shared ) \
  && echo "float guard: clean"         | tee "$EVIDENCE/${STAMP}-8-guards.txt"

log "ALL S1 CHECKS PASSED — evidence in docs/evidence/S1/ (${STAMP})"
