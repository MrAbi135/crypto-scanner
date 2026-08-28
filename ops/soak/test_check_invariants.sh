#!/usr/bin/env bash
# Tests for the acknowledgement triage, and for nothing else in the runner.
#
# The rest of `check_invariants.sh` needs docker, a database and live data, and
# is verified by running it. This part is pure text handling with three rules
# that all fail silently when wrong -- a pattern that never matches, a date
# comparison that is always false, a stale entry nobody notices -- so it is the
# part worth testing away from the host.

set -uo pipefail

cd "$(dirname "$0")/../.." || exit 2

INVARIANTS_LIB_ONLY=1 . ops/soak/check_invariants.sh

failures=0

check() {
  local name="$1" expected="$2" actual="$3"

  if [ "$expected" = "$actual" ]; then
    echo "  ok    $name"
  else
    echo "  FAIL  $name"
    echo "        expected: $expected"
    echo "        actual:   $actual"
    failures=$((failures + 1))
  fi
}

# `problems` is a global the function increments, so the call must NOT sit in a
# command substitution -- a subshell throws the count away and every assertion
# reads zero. The first version of this harness did exactly that: five real
# assertions "passed" as 0 == 0 against a function that was working correctly,
# and the two that happened to expect 0 were the only ones telling the truth.
OUT=$(mktemp)

run() {
  local ack="$1" violations="$2"

  ACK_FILE=$(mktemp)
  printf '%s\n' "$ack" > "$ACK_FILE"
  problems=0

  triage_violations "$violations" > "$OUT"

  rm -f "$ACK_FILE"
}

output() { tr '\n' ';' < "$OUT"; }

FUTURE=$(date -u -d '+30 days' +%F 2>/dev/null || date -u -v+30d +%F)
PAST=$(date -u -d '-1 day' +%F 2>/dev/null || date -u -v-1d +%F)

echo "acknowledgement triage"

# 1. Unacknowledged violations still count. The existence of this file must not
#    have quietly turned the whole check into a formality.
run "# nothing acknowledged" "E. archetype term never met|A1|foo|10|10"
check "an unacknowledged violation counts" "1" "$problems"

# 2. An acknowledged violation stops counting -- and is still printed.
run "E. archetype term never met|A1|foo | $FUTURE | fixed in PR #148" \
    "E. archetype term never met|A1|foo|10|10"
check "an acknowledged violation does not count" "0" "$problems"

case "$(output)" in
  *"~~ known"*) echo "  ok    it is still printed" ;;
  *) echo "  FAIL  it is still printed: $(output)"; failures=$((failures + 1)) ;;
esac

# 3. Past its date it fires again. A fix that missed the date it was given is
#    news, which is the only reason a date is required.
run "E. archetype term never met|A1|foo | $PAST | fixed in PR #148" \
    "E. archetype term never met|A1|foo|10|10"
check "an expired acknowledgement fires" "1" "$problems"

# 4. **The one that matters.** The defect is gone and the line is still here.
#    Left alone the check is blind to that defect returning, and every run
#    reads clean. Two problems here: the unrelated violation, and the stale
#    line.
run "E. archetype term never met|A1|foo | $FUTURE | fixed in PR #148" \
    "F. zone grade cannot score|grade|OTE|3"
check "a stale acknowledgement fires" "2" "$problems"

# 5. An acknowledgement is not a wildcard. It silences the violation it names
#    and nothing adjacent to it.
run "E. archetype term never met|A1|foo | $FUTURE | fixed in PR #148" \
    "$(printf 'E. archetype term never met|A1|foo|10|10\nE. archetype term never met|A3|bar|10|10')"
check "a different term still counts" "1" "$problems"

# 6. Comments and blanks are not patterns. An empty pattern would match every
#    line and silence the entire file.
run "$(printf '# a comment | 2099-01-01 | why\n\nE. archetype term never met|A1|foo | %s | fixed' "$FUTURE")" \
    "E. archetype term never met|A1|foo|10|10"
check "comments are not patterns" "0" "$problems"

# 7. No acknowledgement file at all is the ordinary case, not an error.
ACK_FILE=/nonexistent
problems=0
triage_violations "E. x|1" > "$OUT"
check "a missing file is not an error" "1" "$problems"

# 8. A file with Windows line endings. The repo is edited on Windows and the
#    file is copied to the host by `check_invariants.ps1`; a trailing CR lands
#    inside the last field, so the reason string carries it and -- before the
#    strip existed -- so did the date, which then never compared as expired.
ACK_FILE=$(mktemp)
printf 'E. archetype term never met|A1|foo | %s | fixed
' "$FUTURE" > "$ACK_FILE"
problems=0
triage_violations "E. archetype term never met|A1|foo|10|10" > "$OUT"
check "a CRLF acknowledgement still matches" "0" "$problems"
rm -f "$ACK_FILE"

# 9. The file that ships. Its patterns contain `|`, which is also the field
#    separator, and a naive split would truncate every one of them into
#    something that matches nothing -- silently, and only on the host.
#
#     Both lines are fed, because an acknowledgement that matches nothing fires
#     -- so this doubles as the check that the shipped file has no stale entry
#     left in it. When one of these is deployed and its line deleted, delete
#     the matching violation here too and this stays honest.
ACK_FILE=ops/soak/acknowledged.txt
problems=0
triage_violations "$(printf 'E. archetype term never met|A1|mss_origin_zone_retested|80|80
I. pool strength component cannot vary|touches|1412|0')" > "$OUT"
check "the shipped file's own patterns match, and none is stale" "0" "$problems"

echo
echo "check labels vs the row pattern"

# 10. Every label in the shipped SQL is one the runner will actually count.
#     The pattern said `[A-G]` while the file had grown H, H2 and I; their rows
#     were emitted by psql and dropped here, so three checks were switched off
#     and nothing said so.
problems=0
verify_check_labels
check "the shipped SQL's labels all match" "0" "$problems"

# 11. And the guard can fail. `verify_check_labels` reads a fixed path, so the
#     negative case is asserted against the pattern the guard uses rather than
#     by moving the file the runner needs. Without this, test 10 passes on a
#     pattern that matches everything.
if printf '%s
' "Zed. something" | grep -qE "$VIOLATION_ROW"; then
  echo "  FAIL  a label the pattern cannot match is treated as matching"
  failures=$((failures + 1))
else
  echo "  ok    a label the pattern cannot match is rejected"
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "OK -- triage rules hold"
else
  echo "$failures failure(s)"
fi

rm -f "$OUT"

exit $(( failures > 0 ? 1 : 0 ))
