#!/usr/bin/env bash
# Link the skills of this repository into the user skill folders of Claude Code and Codex.
# Then the skills load in every project folder, not only in this repository.
#
# Usage: tools/install_user_skills.sh [--dry-run] [--prune] [--with-pstack]
#   --dry-run     print the changes, change nothing
#   --prune       remove dangling links that point into <repo>/skills/ (a removed skill). Nothing else.
#   --with-pstack also link the pstack workflow skills as pstack-<name>
#
# The script makes symbolic links. Without --prune it does not delete links.
# Each run also removes approval gates that older versions installed (tools/remove_legacy_gates.py).
# If a target exists and is not a link to this repository, the script stops and names it.
# HOME selects the user folders, so a test can use a temporary HOME.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_real="$(cd "$repo" && pwd -P)"
dry_run=0
prune=0
with_pstack=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --prune) prune=1 ;;
    --with-pstack) with_pstack=1 ;;
    --with-gates) echo "--with-gates: the approval gates were removed. Nothing to install." ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

sources=()
for skill in "$repo"/skills/*/; do
  [[ -f "$skill/SKILL.md" ]] || continue
  # Generated repository pstack links are opt-in for user installation.
  [[ "$(basename "$skill")" == pstack-* ]] && continue
  sources+=("${skill%/}")
done
if [[ $with_pstack == 1 ]]; then
  for skill in "$repo"/plugins/pstack/skills/*/; do
    [[ -f "$skill/SKILL.md" ]] && sources+=("${skill%/}")
  done
fi

targets=("$HOME/.claude/skills" "$HOME/.codex/skills")

if [[ $prune == 1 ]]; then
  for target in "${targets[@]}"; do
    [[ -d "$target" ]] || continue
    for link in "$target"/*; do
      # Only a dangling link whose text points into this repository's skills folder.
      [[ -L "$link" && ! -e "$link" ]] || continue
      dest="$(readlink "$link")"
      if [[ "$dest" == "$repo/skills/"* || "$dest" == "$repo_real/skills/"* ]]; then
        if [[ $dry_run == 1 ]]; then
          echo "would remove stale link $link -> $dest"
        else
          rm -- "$link"
          echo "removed stale link $link"
        fi
      fi
    done
  done
fi

conflicts=0
for target in "${targets[@]}"; do
  for skill in "${sources[@]}"; do
    name="$(basename "$skill")"
    [[ "$skill" == "$repo"/plugins/pstack/skills/* ]] && name="pstack-$name"
    link="$target/$name"
    if [[ -e "$link" || -L "$link" ]]; then
      if [[ "$(readlink -f "$link")" != "$(readlink -f "$skill")" ]]; then
        echo "CONFLICT: $link exists and does not point to $skill. Not changed."
        conflicts=1
      fi
      continue
    fi
    if [[ $dry_run == 1 ]]; then
      echo "would link $link -> $skill"
    else
      mkdir -p "$target"
      ln -s "${skill%/}" "$link"
      echo "linked $link"
    fi
  done
done

cleanup_args=(--home "$HOME")
[[ $dry_run == 1 ]] && cleanup_args+=(--dry-run)
"${PY:-python3}" "$repo/tools/remove_legacy_gates.py" "${cleanup_args[@]}" || conflicts=1
if [[ $with_pstack == 1 ]]; then
  echo "VS Code: use this repository's .github/prompts, or configure chat.promptFilesLocations for its absolute path."
  echo "No ~/.copilot/skills convention is assumed."
fi
exit $conflicts
