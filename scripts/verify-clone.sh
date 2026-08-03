#!/usr/bin/env bash
# Fresh-clone validation — Gate G0 evidence (S0.1 guide §25, DoD).
# Proves: required tools present, lockfiles install frozen, hooks runnable,
# import contracts loadable. Read-only; makes no commits.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
say() { printf '\n=== %s ===\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "MISSING TOOL: $1"; exit 1; }; }

say "1/5 Tools present"
need git; need uv; need node; need corepack
echo "ok"

say "2/5 Backend installs frozen (uv sync --locked)"
( cd backend && uv sync --locked )

say "3/5 Frontend installs frozen (pnpm install --frozen-lockfile)"
corepack enable
( cd frontend && pnpm install --frozen-lockfile )

say "4/5 Import contracts load (import-linter)"
( cd backend && uv run lint-imports )

say "5/5 Pre-commit hooks run clean"
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit run --all-files
else
  echo "pre-commit not installed — run scripts/bootstrap.sh first"; exit 1
fi

say "verify-clone: ALL GREEN"
