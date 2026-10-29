#!/usr/bin/env python3
"""Copy a frozen skill snapshot into one workspace. Dry-run unless --apply.

No symlinks, global settings, credentials or site contracts are installed.
Conflicts are checked before any writes. Existing instructions and Claude settings
are merged; other differing managed files are conflicts, never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "remove_legacy_gates", Path(__file__).resolve().parent / "remove_legacy_gates.py"
)
remove_legacy_gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remove_legacy_gates)
DIRECT_NOTE = "Execution mode: direct. Engineering commands run on this Linux host.\n"
LEGACY_DIRECT_NOTE = (
    "Execution mode: direct. Engineering commands run only on this approved Linux host, "
    "after the required approvals.\n"
)


def codex_config(target: Path, mode: str) -> str:
    """Project Codex config. Direct mode can write the workspace; it still asks."""
    sandbox = "workspace-write" if mode == "direct" else "read-only"
    cwd = json.dumps(str(target))
    return (
        f'sandbox_mode = "{sandbox}"\n'
        "approval_policy = { granular = { sandbox_approval = true, rules = true, mcp_elicitations = true, request_permissions = true, skill_approval = true } }\n"
        "\n"
        "[mcp_servers.reservoir]\n"
        'command = "python3"\n'
        'args = [".reservoir-agent/tools/mcp_server.py"]\n'
        f"cwd = {cwd}\n"
        "startup_timeout_sec = 20\n"
        "tool_timeout_sec = 60\n"
        'default_tools_approval_mode = "prompt"\n'
        'enabled_tools = ["list_skills", "doctor", "show_workflow"]\n'
    )


def plan_install(source: Path, target: Path, mode: str, harness: str) -> tuple[dict[Path, bytes], list[str]]:
    writes = {}
    warnings = []
    skills = [
        p
        for p in sorted((source / "skills").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    if not skills or not (source / "skills/reservoir-engineer/SKILL.md").is_file():
        raise ValueError("source has no reservoir-engineer skill")
    for file in skills:
        if file.is_symlink():
            raise ValueError(f"source skill symlink is not supported: {file.relative_to(source)}")
        relative = file.relative_to(source / "skills")
        content = file.read_bytes()
        writes[target / ".reservoir-agent/skills" / relative] = content
        for folder, name in ((".claude/skills", "claude"), (".agents/skills", "codex")):
            if harness in ("both", name):
                writes[target / folder / relative] = content
    for name in (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "site.contract.example.yml",
        "site.contract.schema.json",
        "profiles/home.example.yml",
        "profiles/company.example.yml",
        "VERSION",
        "CHANGELOG.md",
        "tools/doctor.py",
        "tools/mcp_server.py",
        "tools/read_envelope.py",
    ):
        writes[target / ".reservoir-agent" / name] = (source / name).read_bytes()
    for file in sorted((source / "changes").glob("*.md")):
        writes[target / ".reservoir-agent" / file.relative_to(source)] = file.read_bytes()
    for file in sorted((source / "docs").rglob("*.md")):
        writes[target / ".reservoir-agent" / file.relative_to(source)] = file.read_bytes()
    writes[target / ".reservoir-agent/tools/render_template.py"] = (
        source / "tools/render_template.py"
    ).read_bytes()
    for file in sorted((source / "templates").rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts:
            writes[target / ".reservoir-agent" / file.relative_to(source)] = file.read_bytes()

    mutable = set()
    agent_note = "\n<!-- reservoir-agent workspace -->\nRead `.reservoir-agent/AGENTS.md` and `.reservoir-agent/skills/reservoir-engineer/SKILL.md` first for reservoir tasks.\n"
    agent_note += (
        "Execution mode: manual. Prepare Linux commands for the user; do not run engineering or remote commands from this agent host.\n"
        if mode == "manual"
        else DIRECT_NOTE
    )
    for name, note in (
        ("AGENTS.md", agent_note),
        ("CLAUDE.md", "\n@AGENTS.md\n@.reservoir-agent/AGENTS.md\n"),
    ):
        path = target / name
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if name == "AGENTS.md" and mode == "direct":
            previous = previous.replace(LEGACY_DIRECT_NOTE, DIRECT_NOTE)
        if (
            name == "AGENTS.md"
            and "<!-- reservoir-agent workspace -->" in previous
            and note.strip() not in previous
        ):
            raise ValueError("existing reservoir instruction mode differs; review it by hand")
        writes[path] = (previous if note.strip() in previous else previous + note).encode("utf-8")
        mutable.add(path)

    if harness in ("both", "claude"):
        path = target / ".claude/settings.json"
        settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(settings, dict):
            raise ValueError("Claude settings must be an object")
        permissions = settings.setdefault("permissions", {})
        if permissions.get("defaultMode") == "bypassPermissions":
            raise ValueError("refusing to install over bypassPermissions")
        permissions.setdefault("defaultMode", "plan" if mode == "manual" else "default")
        asks = permissions.setdefault("ask", [])
        if not isinstance(asks, list):
            raise ValueError("Claude permissions.ask must be a list")
        # Manual mode prepares commands for the user, so every shell command asks.
        if mode != "direct":
            for rule in ("Bash(*)", "PowerShell(*)"):
                if rule not in asks:
                    asks.append(rule)
        remove_legacy_gates.strip_legacy_hooks(settings)
        writes[path] = (json.dumps(settings, indent=2) + "\n").encode("utf-8")
        mutable.add(path)
    if harness in ("both", "codex"):
        config = target / ".codex/config.toml"
        if config.exists():
            writes[config] = config.read_bytes()
            warnings.append("Existing Codex config preserved. Verify its sandbox mode and MCP tool list.")
        else:
            writes[config] = codex_config(target, mode).encode()

    server = ".reservoir-agent/tools/mcp_server.py"
    portable = {"mcpServers": {"reservoir": {"command": "python3", "args": [server]}}}
    vscode = {
        "servers": {
            "reservoir": {
                "type": "stdio",
                "command": "python3",
                "args": ["${workspaceFolder}/" + server],
            }
        }
    }
    writes[target / ".mcp.json"] = (json.dumps(portable, indent=2) + "\n").encode()
    writes[target / ".vscode/mcp.json"] = (json.dumps(vscode, indent=2) + "\n").encode()
    command = source / ".claude/commands/linux-direct.md"
    if command.is_file() and harness in ("both", "claude"):
        writes[target / ".claude/commands/linux-direct.md"] = command.read_bytes()

    ignore = target / ".gitignore"
    previous = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    for pattern in ("site.contract.yml", "worklog/", "reports/"):
        if pattern not in previous.splitlines():
            previous = previous.rstrip("\n") + "\n" + pattern + "\n"
    writes[ignore] = previous.encode("utf-8")
    mutable.add(ignore)
    manifest = {
        "format": 1,
        "mode": mode,
        "harness": harness,
        "files": {
            str(p.relative_to(target)).replace(os.sep, "/"): hashlib.sha256(content).hexdigest()
            for p, content in sorted(writes.items())
        },
    }
    writes[target / ".reservoir-agent/install-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    # Preflight every destination, including its parents, before changing any file.
    for path, content in writes.items():
        for parent in (path, *path.parents):
            if parent == target:
                break
            if parent.is_symlink():
                raise ValueError(f"symlink destination refused: {parent.relative_to(target)}")
            if parent != path and parent.exists() and not parent.is_dir():
                raise ValueError(f"destination parent is not a directory: {parent.relative_to(target)}")
        if path.exists() and (not path.is_file() or (path not in mutable and path.read_bytes() != content)):
            raise ValueError(f"conflict (not changed): {path.relative_to(target)}")
    return {
        path: content
        for path, content in writes.items()
        if not path.is_file() or path.read_bytes() != content
    }, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("manual", "direct"), default="manual")
    parser.add_argument("--harness", choices=("both", "claude", "codex"), default="both")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    source, target = args.source.resolve(), args.target.resolve()
    if args.mode == "direct" and sys.platform != "linux":
        print("ERROR: direct mode is supported only on the Linux execution host", file=sys.stderr)
        return 2
    try:
        writes, warnings = plan_install(source, target, args.mode, args.harness)
        if args.apply:
            for path, content in writes.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        print(
            json.dumps(
                {
                    "applied": args.apply,
                    "mode": args.mode,
                    "files": [str(p.relative_to(target)) for p in writes],
                    "warnings": warnings,
                    "limits": [
                        "Workspace-local snapshot, not a global installation or a security sandbox.",
                        "Reload/trust the actual IDE project and verify its permission settings.",
                        "Updates refuse differing snapshot files; install into a fresh workspace or review changes manually.",
                        "Ignored files can still be read/sent by an agent; endpoint approval is required.",
                    ],
                },
                indent=2,
            )
        )
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
