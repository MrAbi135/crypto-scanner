#!/usr/bin/env bash
# One-command local setup for a fresh clone (S0.1 guide §25).
# Idempotent: safe to re-run. No business logic — repo lifecycle only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
say() { printf '\n=== %s ===\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "MISSING TOOL: $1 — see docs/setup.md"; exit 1; }; }

say "1/5 Checking required tools"
need git
need uv
need node
need corepack
echo "tools: ok"

say "2/5 Backend: uv toolchain + dependencies"
( cd backend && uv python install 3.12 && uv sync --locked )

say "3/5 Frontend: pnpm + dependencies"
corepack enable
( cd frontend && pnpm install --frozen-lockfile )

say "4/5 Git hooks (pre-commit + commit-msg)"
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit install
  pre-commit install --hook-type commit-msg
else
  echo "pre-commit not found — install with: uv tool install pre-commit"
fi

say "5/5 Local env file"
if [ ! -f ops/env/dev.env ]; then
  cp ops/env/.env.example ops/env/dev.env
  echo "created ops/env/dev.env from catalog — fill in local values"
else
  echo "ops/env/dev.env already present — left untouched"
fi

say "bootstrap complete — run scripts/verify-clone.sh to validate"
