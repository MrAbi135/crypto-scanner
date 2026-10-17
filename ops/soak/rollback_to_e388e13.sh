#!/usr/bin/env bash
# Roll the VM back from the 2026-10-12 build (#318 s6-ob-v9, #319, #320 off,
# s8-confluence-v37) to e388e13 (release p1b-e388e13, s6-ob-v8, s8-confluence-v36).
# Never run as part of a deploy; it exists so a bad deploy has a tested way back.
#
# Why a separate script: post_soak_deploy.sh cannot do this. Its step 0 runs
# `git pull`, which fails on the detached checkout a rollback needs, and its
# step-0/step-5 greps name the NEW batch, so against e388e13 they would fail by
# design.
#
# What it resets, on purpose: soak T0 (a rollback IS a restart).
# What it does NOT undo, because there is nothing to undo:
#   * schema -- e388e13 and main share head migration 025; nothing to downgrade
#     (checked below, the script stops if that is no longer true);
#   * Redis -- one engine shard uses the same consumer group ("engine") in both
#     builds; the script stops if more than one shard is configured;
#   * rows -- s6-ob-v9 OB rows stay in detection.ict_zones; v8 reads only v8.
#     OBs whose id is the same under both versions are overwritten by v8's
#     first pass. v8's decided-state stops at the deploy candle, so its first
#     pass after the rollback decides every candle since in one go -- the
#     multi-candle case v8's evidence-timing bug lives in.
#
# Dry run by default: prints every command and runs only the read-only checks.
# To execute, on the VM:
#     CONFIRM=rollback-e388e13 RESTART_REASON='rollback: <why>' bash ops/soak/rollback_to_e388e13.sh
# Roll forward again afterwards: `git checkout main`, then post_soak_deploy.sh.

set -uo pipefail

# Step 1 checks out a tree in which this file does not exist. Bash reads a
# script as it runs, so run from a copy outside the checkout rather than trust
# that the unlinked file stays readable.
if [ -z "${ROLLBACK_FROM_COPY:-}" ]; then
  copy=$(mktemp /tmp/rollback_to_e388e13.XXXXXX.sh) || exit 2
  cp "$0" "$copy" || exit 2
  ROLLBACK_FROM_COPY=1 exec bash "$copy" "$@"
fi

cd ~/crypto-scanner || exit 2

TARGET=e388e13
DC="docker compose -f ops/compose/docker-compose.dev.yml"
PSQL="docker exec -i scanner-dev-db-1 psql -U scanner -d scanner -At"
LOG=~/soak-logs/rollback.log
EXECUTE=0
[ "${CONFIRM:-}" = "rollback-${TARGET}" ] && EXECUTE=1

fail() { echo "!! $*"; exit 1; }
step() { echo; echo "== $*"; }
run() {
  if [ "$EXECUTE" = 1 ]; then
    echo "+ $*"
    eval "$@"
  else
    echo "DRY RUN would run: $*"
  fi
}

# ---------------------------------------------------------------------------
step "0. Preconditions (read-only, run in dry run too)"
# ---------------------------------------------------------------------------

[ "$EXECUTE" = 0 ] || [ -n "${RESTART_REASON:-}" ]   || fail "set RESTART_REASON='...' -- a rollback interrupts the soak and the reason belongs in the log"

git cat-file -e "${TARGET}^{commit}" 2>/dev/null   || fail "commit $TARGET is not in this clone"
[ -z "$(git status --porcelain --untracked-files=no)" ]   || fail "tracked files are modified; refuse to check out over them"

from_rev=$(git rev-parse --short HEAD)
from_release=$(docker exec scanner-dev-engine-1 printenv SCANNER_RELEASE 2>/dev/null | tr -d '\r')
started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1 2>/dev/null)   || fail "engine container not found"
echo "   now running : $from_release (checkout $from_rev), engine started $started"
echo "   rolling to  : $TARGET"

shards=$(docker exec scanner-dev-engine-1 printenv SCANNER_ENGINE_SHARD_COUNT 2>/dev/null | tr -d '\r')
[ -z "$shards" ] || [ "$shards" = 1 ]   || fail "SCANNER_ENGINE_SHARD_COUNT=$shards: e388e13 has no shards; undo the sharding first"

# The schema must already be what the target expects, because nothing here downgrades it.
target_head=$(git show "$TARGET:backend/src/scanner/infrastructure/persistence/alembic/versions/" \
  | grep -E '^[0-9]{3}_.*\.py$' | sort | tail -1)
schema_now=$($PSQL -c "select version_num from alembic_version;" | tr -d '\r')
target_rev=$(git show "$TARGET:backend/src/scanner/infrastructure/persistence/alembic/versions/$target_head" \
  | tr -d '\r' | sed -n 's/^revision = "\(.*\)"$/\1/p')
[ -n "$target_rev" ] && [ "$schema_now" = "$target_rev" ]   || fail "schema is '$schema_now' but $TARGET's head is '$target_rev' ($target_head) -- this script does not downgrade"
echo "   schema $schema_now = $TARGET's head ($target_head): nothing to undo"

[ "$EXECUTE" = 1 ] || echo "   (dry run: set CONFIRM=rollback-${TARGET} to execute)"

# ---------------------------------------------------------------------------
step "1. Check out the target"
# ---------------------------------------------------------------------------

run git checkout -q --detach "$TARGET"
if [ "$EXECUTE" = 1 ]; then
  [ "$(git rev-parse --short HEAD)" = "$TARGET" ]   || fail "HEAD is not $TARGET after checkout"
fi

# ---------------------------------------------------------------------------
step "2. Stamp the release the containers will report"
# ---------------------------------------------------------------------------

release="p1b-${TARGET}"
run "sed -i 's|^SCANNER_RELEASE=.*|SCANNER_RELEASE=${release}|' ops/env/dev.env"
if [ "$EXECUTE" = 1 ]; then
  grep -q "^SCANNER_RELEASE=${release}$" ops/env/dev.env   || fail "could not stamp SCANNER_RELEASE=${release}"
fi

# ---------------------------------------------------------------------------
step "3. Build all five images from the target (four images lie otherwise)"
# ---------------------------------------------------------------------------

run "$DC build api engine worker ingest || fail 'build failed'"

# ---------------------------------------------------------------------------
step "4. Restart -- this resets T0"
# ---------------------------------------------------------------------------

run "$DC up -d || fail 'compose up failed'"
run sleep 15

# ---------------------------------------------------------------------------
step "5. Verify the RUNNING containers are the target, not the tree"
# ---------------------------------------------------------------------------

if [ "$EXECUTE" = 1 ]; then
  docker exec scanner-dev-engine-1 grep -qF "s6-ob-v8" /app/src/scanner/application/detection/ict_ob_replay.py   || fail "running engine: OB is not s6-ob-v8"
  docker exec scanner-dev-engine-1 grep -qF "s8-confluence-v36" /app/src/scanner/application/detection/confluence_replay.py   || fail "running engine: confluence is not v36"
  docker exec scanner-dev-engine-1 test ! -e /app/src/scanner/application/detection/pass_candles.py   || fail "running engine still has #319's pass_candles.py"
  docker exec scanner-dev-engine-1 test ! -e /app/src/scanner/application/detection/shards.py   || fail "running engine still has #320's shards.py"

  running_release=$(docker exec scanner-dev-engine-1 printenv SCANNER_RELEASE | tr -d '\r')
  [ "$running_release" = "$release" ]   || fail "running engine reports '$running_release', expected '$release'"

  new_started=$(docker inspect --format '{{.State.StartedAt}}' scanner-dev-engine-1)
  [ "$new_started" != "$started" ]   || fail "engine StartedAt did not change -- it was not restarted"

  for c in scanner-dev-engine-1 scanner-dev-worker-1 scanner-dev-ingest-1 scanner-dev-api-1; do
    [ "$(docker inspect --format '{{.State.Running}}' "$c")" = true ]   || fail "$c is not running"
  done

  schema_after=$($PSQL -c "select version_num from alembic_version;" | tr -d '\r')
  [ "$schema_after" = "$schema_now" ]   || fail "schema moved: $schema_now -> $schema_after"

  mkdir -p ~/soak-logs
  echo "$new_started" > ~/soak-logs/T0
  printf '%s\trollback %s (%s) -> %s\tT0 %s\treason: %s\n' \
    "$(date -u +%FT%TZ)" "$from_release" "$from_rev" "$release" "$new_started" "$RESTART_REASON" >> "$LOG"
  echo "   new T0: $new_started (recorded in ~/soak-logs/T0 and $LOG)"
fi

# ---------------------------------------------------------------------------
step "6. Next 30 minutes, by hand"
# ---------------------------------------------------------------------------

cat <<'EOF'
   docker logs --since 10m scanner-dev-engine-1 2>&1 | grep -c detection_pass_completed      -- passes are flowing
   docker logs --since 10m scanner-dev-engine-1 2>&1 | grep -ci '"level": "error"'          -- want 0
   docker exec scanner-dev-redis-1 redis-cli XPENDING scanner:stream:candle-closed engine     -- want 0 after catch-up
   The first pass per context is a catch-up (every candle since the deploy, in one pass).
   Leaving the detached checkout: `git checkout main` before post_soak_deploy.sh, whose git pull needs a branch.
EOF
