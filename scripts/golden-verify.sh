#!/usr/bin/env bash
# Run the golden gate: every curated dataset, plus the determinism check.
#
# This is THE detector-development workflow (Constitution §32.3-§32.5,
# Roadmap S3). Run it before and after any change to detection logic; a
# refactor that alters golden output is not a refactor, it is a logic change
# requiring a version bump and a spec revision (Constitution §44.5).
set -euo pipefail

cd "$(dirname "$0")/../backend"

exec uv run pytest tests/golden -o addopts="" "$@"
