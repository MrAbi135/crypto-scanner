#!/usr/bin/env bash
# Load the golden datasets into the dev database so the chart can show them.
#
# Constitution §5 verification needs the developer to look at the labelled
# scenario, and the chart reads Postgres. This puts one in reach of the other.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/ops/env/dev.env"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE -- run scripts/bootstrap.sh" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# Compose hostnames do not resolve from the host -- see scripts/cli.sh.
SCANNER_DB_DSN="${SCANNER_DB_DSN/@db:/@localhost:}"
SCANNER_REDIS_URL="${SCANNER_REDIS_URL//\/\/redis:/\/\/localhost:}"
export SCANNER_DB_DSN SCANNER_REDIS_URL

cd "$ROOT/backend"

exec uv run python tools_golden_to_db.py "$@"
