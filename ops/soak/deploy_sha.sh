#!/usr/bin/env bash
# Which commit does post_soak_deploy.sh ship? Exactly DEPLOY_SHA, or nothing.
# Sourced by it in step 0, and by test_deploy_sha.sh.
#
# Step 0 used to `git pull` whenever local main differed from origin/main, so a
# deploy shipped whatever main was at that moment. The owner's rule
# (2026-10-18) is a deploy of one named commit, with main frozen until it is
# verified; this makes the freeze a check instead of a promise.
#
# deploy_sha_verdict <want> <head> <origin_main> <head_is_ancestor_of_want yes|no>
#   all full SHAs; prints one line and returns:
#     0  "ok: ..."      HEAD is already the commit to deploy
#     0  "ff: ..."      HEAD is behind it on the same line -- fast-forward to it
#     1  "refuse: ..."  anything else; the script fails with this line
deploy_sha_verdict() {
  local want="$1" head="$2" origin="$3" behind="$4"

  if [ -z "$want" ]; then
    echo "refuse: no DEPLOY_SHA -- name the exact commit this deploy ships"
    return 1
  fi

  if [ "$origin" != "$want" ]; then
    echo "refuse: origin/main is ${origin:0:7}, DEPLOY_SHA is ${want:0:7} -- main moved after the freeze, or the wrong SHA was given"
    return 1
  fi

  if [ "$head" = "$want" ]; then
    echo "ok: checkout is already at ${want:0:7}"
    return 0
  fi

  if [ "$behind" = "yes" ]; then
    echo "ff: checkout ${head:0:7} is behind ${want:0:7} on the same line -- fast-forward"
    return 0
  fi

  echo "refuse: checkout ${head:0:7} is not an ancestor of ${want:0:7} -- it has diverged; do not deploy over it"
  return 1
}
