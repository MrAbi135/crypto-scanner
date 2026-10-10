#!/usr/bin/env bash
#
# Run ops/soak/signal_outcomes.py against the live database, read-only.
#
# Wrapped in a script for the same reason check_invariants.sh wraps
# break_tolerance.py: the invocation needs the engine image, the compose
# network and `ops/env/dev.env`, which is untracked and exists only in the
# deployed checkout. Typing it by hand is how the wrong network or a missing
# env file turns into "0 signals scored" that reads like a clean result.
#
# Not on cron. This answers a question the owner asks occasionally -- the next
# time is at roughly 290 resolved trades, or 2026-12-10 -- and a tool that runs
# hourly accumulates a log nobody reads.
set -uo pipefail

# Fixed, not relative to this file, and for the same reason check_invariants.sh
# does it: the compose project, the env file and the mounted helper are all
# resolved from here. A run from a git worktree would otherwise mount the live
# checkout's copy of the helper while running this shell from the worktree --
# a mixed build that looks clean. See the note at check_invariants.sh's own cd.
cd ~/crypto-scanner || exit 2

if [ ! -f ops/soak/signal_outcomes.py ]; then
  echo "ops/soak/signal_outcomes.py is missing" >&2
  exit 2
fi

if [ ! -f ops/env/dev.env ]; then
  echo "ops/env/dev.env is missing -- the helper cannot reach the database" >&2
  exit 2
fi

net=$(docker inspect scanner-dev-engine-1 \
        --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null)

if [ -z "$net" ]; then
  echo "cannot read the engine container's network -- is the stack up?" >&2
  exit 2
fi

exec docker run --rm --network "$net" --env-file ops/env/dev.env \
  --entrypoint python \
  -v "$PWD/ops/soak/signal_outcomes.py:/tmp/signal_outcomes.py:ro" \
  -w /app scanner-dev-engine /tmp/signal_outcomes.py
