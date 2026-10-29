#!/usr/bin/env bash
# Update this checkout from its upstream branch and refresh the user skill links.
#
# Usage: tools/update.sh [--dry-run]     (or: make update)
#
# Steps: git fetch; stop if tracked files have changes; git merge --ff-only @{u};
# report the new commits and the added, removed, and changed skills; link new skills and remove
# stale links and remove legacy approval gates (install_user_skills.sh --prune);
# run check_skills.py; run doctor (a doctor failure is only a warning).
# --dry-run fetches and reports, but does not merge, link, or remove.
# PY selects the Python interpreter (default python3). HOME selects the user folders.
set -euo pipefail

# Bash reads a script while it runs. The merge can change this file, so parse everything first.
main() {
  local repo dry_run=0 py="${PY:-python3}"
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=1 ;;
      *) echo "unknown option: $arg" >&2; return 2 ;;
    esac
  done
  cd "$repo"

  if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    echo "ERROR: the current branch has no upstream. Set one with: git branch --set-upstream-to=<remote>/<branch>" >&2
    return 1
  fi
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: tracked files have local changes. Commit or stash them first. Nothing changed." >&2
    git status --short --untracked-files=no >&2
    return 1
  fi

  git fetch --quiet
  local old new
  old="$(git rev-parse HEAD)"
  new="$(git rev-parse '@{u}')"
  echo "version: $(cat VERSION 2>/dev/null || echo unknown) at ${old:0:12}"
  if [[ "$old" == "$new" ]]; then
    echo "Already up to date."
  elif ! git merge-base --is-ancestor "$old" "$new"; then
    echo "ERROR: the local branch has commits that are not upstream. A fast-forward is not possible." >&2
    return 1
  else
    echo "New commits ${old:0:12}..${new:0:12}:"
    git log --oneline "$old..$new"
    report_skills "$old" "$new"
    if [[ $dry_run == 1 ]]; then
      echo "dry run: no merge. Would run: git merge --ff-only @{u}"
    else
      git merge --ff-only --quiet '@{u}'
      echo "updated to ${new:0:12}, version $(cat VERSION 2>/dev/null || echo unknown)"
    fi
  fi

  local install_args=(--prune)
  [[ $dry_run == 1 ]] && install_args+=(--dry-run)
  PY="$py" bash "$repo/tools/install_user_skills.sh" "${install_args[@]}"

  if [[ $dry_run == 1 ]]; then
    echo "dry run: skipped check_skills.py and doctor."
    return 0
  fi
  "$py" "$repo/tools/check_skills.py"
  local contract="$repo/site.contract.yml"
  if ! "$py" "$repo/tools/doctor.py" --mode agent --contract "$contract"; then
    echo "WARNING: doctor is not ready (see above). The update is complete."
  fi
}

report_skills() {
  local old="$1" new="$2" name
  local -a before after
  mapfile -t before < <(git ls-tree -d --name-only "$old" skills/ | sed 's#^skills/##' | sort)
  mapfile -t after < <(git ls-tree -d --name-only "$new" skills/ | sed 's#^skills/##' | sort)
  for name in $(comm -13 <(printf '%s\n' "${before[@]}") <(printf '%s\n' "${after[@]}")); do
    echo "skill added: $name"
  done
  for name in $(comm -23 <(printf '%s\n' "${before[@]}") <(printf '%s\n' "${after[@]}")); do
    echo "skill removed: $name"
  done
  for name in $(comm -12 <(printf '%s\n' "${before[@]}") <(printf '%s\n' "${after[@]}")); do
    if ! git diff --quiet "$old" "$new" -- "skills/$name"; then
      echo "skill changed: $name"
    fi
  done
}

main "$@"
exit $?
