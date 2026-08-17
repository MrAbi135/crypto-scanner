#!/usr/bin/env bash
# Run a scanner CLI command against the dev stack (Sprint S3b).
#
# Wraps the two things every invocation needs and nobody remembers: the dev
# environment (ops/env/dev.env, per S0.2 §7) and the backend venv. Without the
# first the command cannot find the database; without the second it runs
# against system Python and fails on the first import.
#
#   scripts/cli.sh warmth
#   scripts/cli.sh sync-symbols
#   scripts/cli.sh backfill --symbol BTCUSDT --timeframe H1 --start 2026-06-01
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/ops/env/dev.env"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE -- run scripts/bootstrap.sh" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# dev.env addresses the services by their compose hostnames, which is correct
# for a container and unresolvable from the host. Every host-run command that
# touches Postgres or Redis therefore dies on getaddrinfo -- including alembic,
# which scripts/with-env.sh explicitly exists to serve.
#
# Rewritten here rather than in dev.env: the file is right for the processes it
# configures, and giving it host addresses would break the containers to fix
# the host. Only the published ports are assumed, and compose publishes both.
SCANNER_DB_DSN="${SCANNER_DB_DSN/@db:/@localhost:}"
SCANNER_REDIS_URL="${SCANNER_REDIS_URL/\/\/redis:/\/\/localhost:}"
export SCANNER_DB_DSN SCANNER_REDIS_URL

cd "$ROOT/backend"

exec uv run python -m scanner.runtime.cli "$@"
