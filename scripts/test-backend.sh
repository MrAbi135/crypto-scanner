#!/usr/bin/env bash
# Backend test loop (S0.2 §10). `--lint` runs ruff+mypy+import-linter first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [ "${1:-}" = "--lint" ]; then
  shift
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run mypy
  uv run lint-imports
fi

uv run pytest -m "not integration" "$@"
