#!/usr/bin/env bash
# Tests for deploy_sha_verdict (runs in CI via the shell job).
set -uo pipefail
cd "$(dirname "$0")" || exit 2
. ./deploy_sha.sh

A=ba2d9eb296b1efe30b5878b4ce321bce799da3ad   # the commit to deploy
B=82890ca0000000000000000000000000000000000   # an older commit on the same line
C=c0ffee00000000000000000000000000000000000   # a newer main, or a diverged checkout

failures=0
t() {  # t <label> <want> <head> <origin> <behind> <expected exit> <expected prefix>
  local label="$1" out got
  out=$(deploy_sha_verdict "$2" "$3" "$4" "$5"); got=$?
  if [ "$got" = "$6" ] && [[ "$out" == "$7"* ]]; then
    echo "  ok    $label -> $out"
  else
    echo "  FAIL  $label (want exit $6, '$7...') -> exit $got: $out"; failures=$((failures + 1))
  fi
}

echo "deploy SHA verdict"
t "checkout already at the SHA, origin agrees"   "$A" "$A" "$A" no  0 "ok:"
t "checkout behind on the same line -> ff"       "$A" "$B" "$A" yes 0 "ff:"
t "origin/main moved past the SHA -> refuse"     "$A" "$A" "$C" no  1 "refuse: origin/main"
t "origin moved AND checkout behind -> refuse"   "$A" "$B" "$C" yes 1 "refuse: origin/main"
t "checkout diverged -> refuse"                  "$A" "$C" "$A" no  1 "refuse: checkout"
t "no DEPLOY_SHA -> refuse"                      ""   "$A" "$A" no  1 "refuse: no DEPLOY_SHA"

echo
[ "$failures" -eq 0 ] && echo "OK -- deploy SHA holds" || echo "$failures failure(s)"
exit $(( failures > 0 ? 1 : 0 ))
