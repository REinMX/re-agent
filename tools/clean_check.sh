#!/usr/bin/env bash
# Run a make target in a fresh worktree, so files that exist only on this machine
# (venvs, run output, anything ignored or untracked) cannot change the result.
#
#   tools/clean_check.sh [TARGET [MAKE ARGS...]]
#       HEAD plus your uncommitted edits and new files that are not ignored.
#   tools/clean_check.sh --ref REF [TARGET [MAKE ARGS...]]
#       exactly that commit (the pre-push hook uses this).
set -euo pipefail

ref=""
if [[ ${1:-} == --ref ]]; then
  ref=$2
  shift 2
fi
target=${1:-check-here}
[[ $# -gt 0 ]] && shift

root=$(git rev-parse --show-toplevel)
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
tmp=$(mktemp -d "${TMPDIR:-/tmp}/clean-check.XXXXXX")
cleanup() {
  git -C "$root" worktree remove --force "$tmp/tree" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  git -C "$root" worktree prune
}
trap cleanup EXIT

git -C "$root" worktree add --quiet --detach "$tmp/tree" "${ref:-HEAD}"
if [[ -z $ref ]]; then
  (
    cd "$root"
    { git diff -z --name-only --no-renames --diff-filter=d HEAD; git ls-files -z --others --exclude-standard; } \
      | tar --null --no-recursion -T - -cf -
  ) | tar -xf - -C "$tmp/tree"
  (cd "$root" && git diff -z --name-only --no-renames --diff-filter=D HEAD) \
    | (cd "$tmp/tree" && xargs -0 -r rm -f --)
fi

echo "clean-check: make $target in a fresh worktree of ${ref:-HEAD + uncommitted edits}"
make --no-print-directory -C "$tmp/tree" "$target" "$@"
