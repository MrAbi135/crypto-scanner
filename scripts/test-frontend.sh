#!/usr/bin/env bash
# Frontend test loop (S0.2 §10).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"
pnpm lint
pnpm typecheck
pnpm test
