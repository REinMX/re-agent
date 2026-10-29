#!/usr/bin/env python3
"""Map an ERT config and check its file references. Read-only. Standard library only.

It follows INCLUDE lines, replaces DEFINE variables, and lists the key settings, the hooks,
and the forward models in order. It checks that referenced files exist:
INCLUDE, INSTALL_JOB, LOAD_WORKFLOW, LOAD_WORKFLOW_JOB, RUN_TEMPLATE source, OBS_CONFIG.
It checks that each HOOK_WORKFLOW names a workflow that the config loads.

It does not load the config the way ERT does. For that, use check_ert_config.py.

Usage:
    ert_config_map.py <config.ert> [--json]

A path with a variable that this script cannot replace is listed as "unresolved". It is not an error.
A missing file under fmuconfig/output/ is a warning when a PRE_EXPERIMENT hook exists. The hook probably
creates the file, but ERT checks it when it loads the config, before any hook runs. ERT stops until the
file exists. Use --generated TEXT for other generated paths.
Exit code: 0 = no problems, 1 = problems, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

KEY_SETTINGS = {
    "NUM_REALIZATIONS",
    "RUNPATH",
    "ENSPATH",
    "ECLBASE",
    "QUEUE_SYSTEM",
    "MAX_RUNTIME",
    "MIN_REALIZATIONS",
    "MAX_SUBMIT",
    "NUM_CPU",
    "RANDOM_SEED",
    "OBS_CONFIG",
    "DATA_FILE",
    "REFCASE",
    "RUNPATH_FILE",
    "ENKF_ALPHA",
    "STD_CUTOFF",
    "UPDATE_LOG_PATH",
}
VAR_RE = re.compile(r"<[A-Za-z0-9_]+>")


def strip_comment(line: str) -> str:
    """Cut a line at the first '--' outside double quotes."""
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes and line.startswith("--", index):
            return line[:index]
    return line


def tokens(text: str) -> list[str]:
    """Split on spaces outside double quotes and outside parentheses. Remove the quotes."""
    out: list[str] = []
    current: list[str] = []
    in_quotes = False
    depth = 0
    for char in text.strip():
        if char == '"':
            in_quotes = not in_quotes
            continue
        if not in_quotes:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char.isspace() and depth == 0:
                if current:
                    out.append("".join(current))
                    current = []
                continue
        current.append(char)
    if current:
        out.append("".join(current))
    return out


class Mapper:
    def __init__(self, root_config: Path) -> None:
        self.root = root_config.resolve()
        self.defines: dict[str, str] = {}
        self.settings: dict[str, dict] = {}
        self.forward_models: list[dict] = []
        self.hooks: list[dict] = []
        self.loaded_workflows: dict[str, dict] = {}
        self.includes: list[dict] = []
        self.parameters: list[dict] = []
        self.refs: list[dict] = []
        self.problems: list[dict] = []
        self._stack: list[Path] = []
        self.generated: list[str] = []  # set by --generated. Always a note.
        self.hook_generated: list[str] = ["/fmuconfig/output/"]  # a note only with a PRE_EXPERIMENT hook
        self.deferred: list[tuple[Path, int, str, str]] = []

    def substitute(self, value: str, config_dir: Path) -> str:
        # <CONFIG_PATH> is the folder of the main config, also inside an included file.
        # A plain relative path is relative to the file that holds it. Drogon relies on both rules.
        builtins = {"<CONFIG_PATH>": str(self.root.parent), "<CONFIG_FILE>": self.root.name}
        for _ in range(10):  # DEFINE values can contain other variables
            before = value
            for name, replacement in {**self.defines, **builtins}.items():
                value = value.replace(name, replacement)
            if value == before:
                break
        return os.path.expandvars(value)

    def add_problem(self, file: Path, line: int, message: str, severity: str = "error") -> None:
        self.problems.append({"file": str(file), "line": line, "severity": severity, "message": message})

    def check_ref(self, kind: str, raw: str, config: Path, line: int) -> None:
        resolved = self.substitute(raw, config.parent)
        entry = {"kind": kind, "raw": raw, "file": str(config), "line": line}
        if VAR_RE.search(resolved):
            entry["status"] = "unresolved"
            entry["path"] = resolved
        else:
            path = Path(resolved)
            if not path.is_absolute():
                path = config.parent / path
            path = Path(os.path.normpath(path))
            entry["path"] = str(path)
            entry["status"] = "ok" if path.exists() else "missing"
            if not path.exists():
                self.deferred.append((config, line, kind, str(path)))
        self.refs.append(entry)

    def parse(self, config: Path) -> None:
        if config in self._stack:
            self.add_problem(config, 0, "circular INCLUDE")
            return
        self._stack.append(config)
        try:
            text = config.read_text(errors="replace")
        except OSError as error:
            self.add_problem(config, 0, f"cannot read: {error}")
            self._stack.pop()
            return
        for number, raw_line in enumerate(text.splitlines(), start=1):
            line = strip_comment(raw_line).strip()
            if not line:
                continue
            parts = tokens(line)
            if not parts:
                continue
            key, args = parts[0], parts[1:]
            where = {"file": str(config), "line": number}
            if key == "DEFINE" and len(args) >= 2:
                self.defines[args[0]] = self.substitute(" ".join(args[1:]), config.parent)
            elif key == "INCLUDE" and args:
                self.check_ref("INCLUDE", args[0], config, number)
                target = self.refs[-1]
                self.includes.append({**where, "target": target["path"], "status": target["status"]})
                if target["status"] == "ok":
                    self.parse(Path(target["path"]))
            elif key == "INSTALL_JOB" and len(args) >= 2:
                self.check_ref("INSTALL_JOB", args[1], config, number)
            elif key == "LOAD_WORKFLOW_JOB" and args:
                self.check_ref("LOAD_WORKFLOW_JOB", args[0], config, number)
            elif key == "LOAD_WORKFLOW" and args:
                self.check_ref("LOAD_WORKFLOW", args[0], config, number)
                name = args[1] if len(args) > 1 else Path(self.substitute(args[0], config.parent)).name
                self.loaded_workflows[name] = where
            elif key == "HOOK_WORKFLOW" and len(args) >= 2:
                self.hooks.append({"workflow": args[0], "point": args[1], **where})
            elif key == "FORWARD_MODEL" and args:
                rest = line[len("FORWARD_MODEL") :].strip()
                step = re.match(r"[\w-]+", rest)
                self.forward_models.append({"step": step.group(0) if step else rest, "call": rest, **where})
            elif key == "RUN_TEMPLATE" and args:
                self.check_ref("RUN_TEMPLATE", args[0], config, number)
            elif key == "OBS_CONFIG" and args:
                self.settings[key] = {"value": args[0], **where}
                self.check_ref("OBS_CONFIG", args[0], config, number)
            elif key in {"GEN_KW", "SURFACE", "GEN_DATA", "SUMMARY", "FIELD"} and args:
                options = [self.substitute(a, config.parent) for a in args[1:]]
                self.parameters.append({"keyword": key, "name": args[0], "options": options, **where})
            elif key in KEY_SETTINGS and args:
                self.settings[key] = {"value": " ".join(args), **where}
            elif key in ("QUEUE_OPTION", "ANALYSIS_SET_VAR"):
                self.settings.setdefault(key, {"value": [], **where})["value"].append(" ".join(args))
        self._stack.pop()

    def finish(self) -> None:
        pre_experiment = any(hook["point"] == "PRE_EXPERIMENT" for hook in self.hooks)
        for config, line, kind, path in self.deferred:
            # self.generated and hook_generated hold forward-slash text (Drogon convention).
            # path comes from os.path.normpath, which is backslash on Windows. Compare on
            # forward slashes so a Windows run finds the same generated-path matches as Linux.
            path_posix = path.replace("\\", "/")
            if any(part in path_posix for part in self.generated) or (
                pre_experiment and any(part in path_posix for part in self.hook_generated)
            ):
                self.add_problem(
                    config,
                    line,
                    f"{kind} file not found: {path}. A PRE_EXPERIMENT hook may create it, but ERT checks the file"
                    " when it loads the config, before any hook runs. ERT stops until the file exists",
                    severity="warning",
                )
            else:
                self.add_problem(config, line, f"{kind} file not found: {path}")
        for hook in self.hooks:
            if hook["workflow"] not in self.loaded_workflows:
                self.add_problem(
                    Path(hook["file"]),
                    hook["line"],
                    f"HOOK_WORKFLOW names '{hook['workflow']}', but no LOAD_WORKFLOW loads it",
                )
        if "NUM_REALIZATIONS" not in self.settings:
            self.add_problem(self.root, 0, "NUM_REALIZATIONS is not set in the config or its includes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--generated",
        action="append",
        default=[],
        metavar="TEXT",
        help="A path with TEXT is created by a workflow, so a missing file there is a warning. Repeat for more.",
    )
    args = parser.parse_args(argv)
    if not args.config.is_file():
        print(f"ERROR: not a file: {args.config}", file=sys.stderr)
        return 2

    mapper = Mapper(args.config)
    mapper.generated += args.generated
    mapper.parse(mapper.root)
    mapper.finish()
    result = {
        "config": str(mapper.root),
        "settings": mapper.settings,
        "hooks": mapper.hooks,
        "forward_models": mapper.forward_models,
        "parameters_and_responses": mapper.parameters,
        "includes": mapper.includes,
        "references": mapper.refs,
        "problems": mapper.problems,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Config: {mapper.root}")
        for key, item in sorted(mapper.settings.items()):
            print(f"  {key} = {item['value']}   ({Path(item['file']).name}:{item['line']})")
        print(f"Includes: {len(mapper.includes)}")
        print("Hooks:")
        for hook in mapper.hooks:
            print(f"  {hook['point']:<16} {hook['workflow']}   ({Path(hook['file']).name}:{hook['line']})")
        print("Forward models, in order:")
        for number, item in enumerate(mapper.forward_models, start=1):
            print(f"  {number:>2}. {item['step']}   ({Path(item['file']).name}:{item['line']})")
        unresolved = [r for r in mapper.refs if r["status"] == "unresolved"]
        print(f"File references: {len(mapper.refs)} ({len(unresolved)} unresolved because of variables)")
        for problem in mapper.problems:
            label = "PROBLEM" if problem["severity"] == "error" else "WARNING"
            print(f"{label} {Path(problem['file']).name}:{problem['line']}: {problem['message']}")
        errors = [p for p in mapper.problems if p["severity"] == "error"]
        print(f"{len(errors)} problems, {len(mapper.problems) - len(errors)} warnings")
    return 1 if any(p["severity"] == "error" for p in mapper.problems) else 0


if __name__ == "__main__":
    sys.exit(main())
