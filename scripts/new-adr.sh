#!/usr/bin/env bash
# Scaffold the next numbered ADR from the template (S0.1 guide §25).
# Usage: scripts/new-adr.sh "short decision title"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADR_DIR="$ROOT/docs/adr"
[ $# -ge 1 ] || { echo "usage: $0 \"short decision title\""; exit 1; }
TITLE="$*"

# Next number = highest existing NNN + 1, zero-padded to 3.
last="$(ls "$ADR_DIR" 2>/dev/null | grep -E '^[0-9]{3}-' | sed -E 's/^([0-9]{3}).*/\1/' | sort -n | tail -1 || true)"
next="$(printf '%03d' $(( 10#${last:-000} + 1 )))"

# Slug: lowercase, non-alnum -> hyphen, collapse/trim hyphens.
slug="$(printf '%s' "$TITLE" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')"
out="$ADR_DIR/${next}-${slug}.md"
[ -e "$out" ] && { echo "already exists: $out"; exit 1; }

sed -e "s/ADR-NNN — <short decision title>/ADR-${next} — ${TITLE}/" \
    "$ADR_DIR/template.md" > "$out"
echo "created $out"
