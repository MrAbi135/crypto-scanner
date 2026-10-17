#!/usr/bin/env bash
# Tests for the image-id verification in post_soak_deploy.sh (step 3 records,
# step 5 checks), run against the script's own text.
#
# The two blocks are cut out between their `# >>> image-verify:*` and
# `# <<< image-verify:*` markers and run with `docker` and `fail` stubbed, so
# what is tested is exactly what the deploy runs -- not a copy that could drift.
# The rest of the script needs a VM, a database and a soak to run; this part is
# plain comparison logic that fails silently when wrong (a check that always
# passes looks like a clean deploy), so it is the part worth testing here.

set -uo pipefail

cd "$(dirname "$0")/../.." || exit 2

SCRIPT=ops/soak/post_soak_deploy.sh

block() {
  awk -v start="# >>> image-verify:$1" -v stop="# <<< image-verify:$1" \
    'index($0, start) == 1 {on = 1; next} index($0, stop) == 1 {on = 0} on' "$SCRIPT"
}

RECORD=$(block record)
CHECK=$(block check)

failures=0

# Each marker exactly once. Without its closing marker a block runs to the end
# of the deploy script, and the test then failed only by accident -- on some
# later line of the script -- rather than because the block was broken.
for marker in ">>> image-verify:record" "<<< image-verify:record" ">>> image-verify:check" "<<< image-verify:check"; do
  n=$(grep -c "^# $marker" "$SCRIPT")
  if [ "$n" -ne 1 ]; then
    echo "  FAIL  '# $marker' appears $n times in $SCRIPT, need exactly 1"
    exit 1
  fi
done

if [ -z "$RECORD" ] || [ -z "$CHECK" ]; then
  echo "  FAIL  a block between the markers is empty in $SCRIPT -- nothing under test"
  exit 1
fi

# run <expected exit 0|1> <label> NAME=IMAGE ...
#   NAME is a running container (scanner-dev-<svc>-1); unnamed ones are missing.
#   Every service's freshly built image is BUILT_<svc>.
run() {
  local want="$1" label="$2"
  shift 2

  local out got
  out=$(
    declare -A RUNNING=()
    for pair in "$@"; do RUNNING[${pair%%=*}]=${pair#*=}; done

    fail() { echo "!! $*"; exit 1; }
    docker() {
      case "$1" in
        image) echo "BUILT_${5#scanner-dev-}" ;;              # docker image inspect -f '{{.Id}}' scanner-dev-<svc>
        inspect) echo "${RUNNING[$4]:-}" ;;                    # docker inspect -f '{{.Image}}' scanner-dev-<svc>-1
      esac
    }

    eval "$RECORD"
    eval "$CHECK"
  )
  got=$?

  if [ "$got" = "$want" ]; then
    echo "  ok    $label"
  else
    echo "  FAIL  $label (want exit $want, got $got)"
    echo "$out" | sed 's/^/        /'
    failures=$((failures + 1))
  fi
}

ALL_NEW=(scanner-dev-api-1=BUILT_api scanner-dev-engine-1=BUILT_engine scanner-dev-worker-1=BUILT_worker scanner-dev-ingest-1=BUILT_ingest)

echo "image-id verification"

run 0 "all four backend containers run this deploy's images" "${ALL_NEW[@]}" scanner-dev-frontend-1=OLD_frontend
run 0 "frontend on a new image changes nothing" "${ALL_NEW[@]}" scanner-dev-frontend-1=BUILT_frontend
run 1 "ingest left on an old image fails" scanner-dev-api-1=BUILT_api scanner-dev-engine-1=BUILT_engine scanner-dev-worker-1=BUILT_worker scanner-dev-ingest-1=OLD_ingest
run 1 "api left on an old image fails" scanner-dev-api-1=OLD_api scanner-dev-engine-1=BUILT_engine scanner-dev-worker-1=BUILT_worker scanner-dev-ingest-1=BUILT_ingest
run 1 "a missing worker container fails" scanner-dev-api-1=BUILT_api scanner-dev-engine-1=BUILT_engine scanner-dev-ingest-1=BUILT_ingest
run 1 "two services swapped fails" scanner-dev-api-1=BUILT_engine scanner-dev-engine-1=BUILT_api scanner-dev-worker-1=BUILT_worker scanner-dev-ingest-1=BUILT_ingest

# The frontend is reported, never enforced (owner ruling 2026-10-17), and the
# report must say which way it is.
report=$(
  declare -A RUNNING=([scanner-dev-api-1]=BUILT_api [scanner-dev-engine-1]=BUILT_engine [scanner-dev-worker-1]=BUILT_worker [scanner-dev-ingest-1]=BUILT_ingest [scanner-dev-frontend-1]=OLD_frontend)
  fail() { echo "!! $*"; exit 1; }
  docker() { case "$1" in image) echo "BUILT_${5#scanner-dev-}" ;; inspect) echo "${RUNNING[$4]:-}" ;; esac; }
  eval "$RECORD"; eval "$CHECK"
)
case "$report" in
  *"frontend NOT updated by this deploy"*) echo "  ok    an old frontend is reported as not updated" ;;
  *) echo "  FAIL  an old frontend is reported as not updated: $report"; failures=$((failures + 1)) ;;
esac

echo
if [ "$failures" -eq 0 ]; then
  echo "OK -- image verification holds"
else
  echo "$failures failure(s)"
fi

exit $(( failures > 0 ? 1 : 0 ))
