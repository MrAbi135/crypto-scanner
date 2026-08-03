#!/usr/bin/env bash
# Source ops/env/dev.env, then exec the given command (S0.2 §7, §10).
# The only sanctioned way host-run tooling (alembic, ad-hoc scripts) gets env —
# application code never loads dotenv itself (S0.1 §13 law).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/ops/env/dev.env"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE — run scripts/bootstrap.sh"; exit 1; }
[ $# -ge 1 ] || { echo "usage: $0 <command> [args...]"; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
exec "$@"
