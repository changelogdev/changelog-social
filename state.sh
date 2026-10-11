#!/usr/bin/env bash
# Keeps the bot's saved progress (drafts, what's been posted, records) on a
# separate `state` branch as a single commit, so main's history stays clean.
#   state.sh load   -> unpacks the branch into ./state (empty on the first run)
#   state.sh save   -> replaces the branch with the contents of ./state
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

case "${1:-}" in
  load)
    rm -rf state && mkdir -p state
    if git fetch -q --depth=1 origin state 2>/dev/null; then
      git archive FETCH_HEAD | tar -x -C state
      git rev-parse FETCH_HEAD^{tree} > .git/state-tree
    else
      echo "No state branch yet; starting fresh."
      : > .git/state-tree
    fi
    ;;
  save)
    export GIT_INDEX_FILE="$PWD/.git/state-index"
    rm -f "$GIT_INDEX_FILE"
    tree=$(cd state && git --git-dir=../.git --work-tree=. add -A . && git --git-dir=../.git write-tree)
    if [ "$tree" = "$(cat .git/state-tree 2>/dev/null)" ]; then
      echo "State unchanged."
      exit 0
    fi
    commit=$(GIT_AUTHOR_NAME=changelog-social GIT_AUTHOR_EMAIL=changelog-social@users.noreply.github.com \
             GIT_COMMITTER_NAME=changelog-social GIT_COMMITTER_EMAIL=changelog-social@users.noreply.github.com \
             git commit-tree "$tree" -m "state $(date -u +%Y-%m-%dT%H:%MZ)")
    git push -q --force origin "$commit:refs/heads/state"
    echo "$tree" > .git/state-tree
    echo "State saved."
    ;;
  *)
    echo "usage: state.sh load|save" >&2
    exit 2
    ;;
esac
