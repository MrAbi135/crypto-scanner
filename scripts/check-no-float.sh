#!/usr/bin/env bash
# no-float guard (S0.3 §5): a belt-and-suspenders to the type system.
# `float` in a signature/annotation or a float() conversion inside the pure
# decimal core (shared + domain) is a defect (Constitution §45.8). The
# isinstance-based float *rejection* in decimal_math is legitimate and is not
# matched by these patterns.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS=("$ROOT/backend/src/scanner/shared" "$ROOT/backend/src/scanner/domain")

if grep -rnE --include='*.py' --exclude-dir=__pycache__ \
    '(->|:)[[:space:]]*float\b|list\[float\]|\bfloat\(' "${TARGETS[@]}"; then
  echo "no-float guard: FAIL — float in a signature/conversion above (Constitution §45.8)"
  exit 1
fi
echo "no-float guard: clean"
