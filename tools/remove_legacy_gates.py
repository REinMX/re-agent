#!/usr/bin/env python3
"""Remove the approval gates that older versions of this repository installed.

User: strip each hook whose command contains gate_hook.py from <home>/.claude/settings.json and
delete <home>/.codex/rules/re-agent-gates.rules.
Installed folder (--dest): strip the same hooks from <dest>/.claude/settings.json and delete the
legacy gate files. Other keys and hooks stay. A changed settings file is first copied to
<file>.bak (or <file>.bak.N when that name is taken). A second run changes nothing.
Malformed JSON is reported and left unchanged. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HOOK_MARKER = "gate_hook.py"
HOME_FILES = (".codex/rules/re-agent-gates.rules",)
DEST_FILES = (
    "tools/gate_hook.py",
    "tools/gate_manifest.py",
    "tools/gate_manifest.json",
    "tools/install_gates.py",
    ".codex/rules/gates.rules",
    ".codex/rules/reservoir-gates.rules",
    ".reservoir-agent/tools/gate_hook.py",
    ".reservoir-agent/tools/gate_manifest.py",
    ".reservoir-agent/tools/gate_manifest.json",
)


def strip_legacy_hooks(settings: dict) -> list[str]:
    """Remove gate hooks from settings in place. Return the removed commands."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    removed = []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = []
            for entry in entries:
                command = entry.get("command") if isinstance(entry, dict) else None
                if isinstance(command, str) and HOOK_MARKER in command:
                    removed.append(command)
                else:
                    kept.append(entry)
            if len(kept) == len(entries):
                kept_groups.append(group)
            elif kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if len(kept_groups) != len(groups):
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
    if removed and not hooks:
        del settings["hooks"]
    return removed


def _backup_path(path: Path) -> Path:
    backup = path.with_name(path.name + ".bak")
    number = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.{number}")
        number += 1
    return backup


def clean_settings(path: Path, dry_run: bool) -> list[str]:
    if not path.is_file():
        return []
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"WARNING {path}: not valid JSON ({error}). Not changed."]
    if not isinstance(settings, dict):
        return [f"WARNING {path}: top level is not an object. Not changed."]
    removed = strip_legacy_hooks(settings)
    if not removed:
        return []
    verb = "WOULD REMOVE" if dry_run else "REMOVED"
    if not dry_run:
        shutil.copy2(path, _backup_path(path))
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return [f"{verb} hook {command} from {path}" for command in removed]


def delete_files(root: Path, names: tuple[str, ...], dry_run: bool) -> list[str]:
    actions = []
    for name in names:
        path = root / name
        if not (path.is_file() or path.is_symlink()):
            continue
        if not dry_run:
            path.unlink()
            parent = path.parent
            if parent.name == "rules" and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        actions.append(f"{'WOULD DELETE' if dry_run else 'DELETED'} {path}")
    return actions


def clean(home: Path | None, dest: Path | None, dry_run: bool) -> list[str]:
    actions = []
    if home is not None:
        actions += clean_settings(home / ".claude/settings.json", dry_run)
        actions += delete_files(home, HOME_FILES, dry_run)
    if dest is not None:
        actions += clean_settings(dest / ".claude/settings.json", dry_run)
        actions += delete_files(dest, DEST_FILES, dry_run)
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--dest", type=Path, help="an installed repository or workspace folder")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    actions = clean(args.home, args.dest, args.dry_run)
    for line in actions or ["nothing to clean"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
