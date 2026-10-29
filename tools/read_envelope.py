#!/usr/bin/env python3
"""Allow one named read command when a human grant matches it exactly.

This is not a sandbox. A missing grant, an expired grant, an unknown flag,
or a path outside the grant is an ask.
The checker never runs the command.

    python3 tools/read_envelope.py --command '<exact command>'
    exit 0  allow
    exit 2  ask

The grant is worklog/read-envelope.txt unless --grant names another file.
"""

from __future__ import annotations

import argparse
import glob
import re
import shlex
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_QC = (ROOT / "skills/case-qc/scripts/case_qc.py").resolve()
DEFAULT_GRANT = ROOT / "worklog/read-envelope.txt"
OPAQUE = ("$(", "`", "${", "<<", "<(")
OPERATORS = {";", "&&", "||", "|", "&"}
PYTHON_NOVALUE = {
    "-u",
    "-O",
    "-OO",
    "-B",
    "-S",
    "-E",
    "-I",
    "-s",
    "-v",
    "-q",
    "-b",
    "-bb",
    "-t",
    "-tt",
    "-x",
    "-i",
    "-d",
}
PYTHON_NAME = re.compile(r"^python(\d+(\.\d+)?)?$")
TIME_INDEX = {"raw", "daily", "monthly", "yearly", "first", "last"}
KEY_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(:[A-Za-z0-9_*]+)?$")
REALIZATIONS = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")
PATH_FLAGS = {"--config", "--obs", "--deck", "--include", "--metadata", "--casepath"}
GLOB_FLAGS = {"--runpaths", "--prior"}
REPEATABLE = {"--obs", "--include"}
INPUT_FLAGS = PATH_FLAGS | GLOB_FLAGS


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Grant:
    output_root: Path
    roots: tuple[Path, ...]


def classify(command: str, *, grant_path: Path, today: date, cwd: Path) -> Decision:
    """Return allow only when command, grant, and paths all match. Never execute."""
    grant, errors = load_grant(grant_path, today)
    if grant is None:
        return Decision(False, tuple(errors))
    reasons = command_reasons(command, grant, cwd)
    return Decision(not reasons, tuple(reasons))


def load_grant(path: Path, today: date) -> tuple[Grant | None, list[str]]:
    if not path.is_file():
        return None, ["no read grant"]
    fields: dict[str, list[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return None, [f"cannot read the grant ({error.__class__.__name__})"]
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        key, sep, value = text.partition(":")
        if not sep or not key.strip() or not value.strip():
            return None, ["grant line is not 'key: value'"]
        fields.setdefault(key.strip(), []).append(value.strip())
    unknown = set(fields) - {"owner", "expires", "output_root", "root", "script"}
    if unknown:
        return None, [f"unknown grant field: {', '.join(sorted(unknown))}"]
    owner = fields.get("owner", [""])[0]
    if not owner:
        return None, ["grant has no owner"]
    if fields.get("script") != ["case-qc"]:
        return None, ["grant does not name case-qc"]
    try:
        expires = date.fromisoformat(fields.get("expires", [""])[0])
    except ValueError:
        return None, ["grant expiry is missing or not YYYY-MM-DD"]
    if expires < today:
        return None, ["grant is expired"]
    output = _absolute(fields.get("output_root", [""])[0])
    parsed_roots = []
    for item in fields.get("root", []):
        root = _absolute(item)
        if root is None:
            return None, ["grant roots and output_root must be absolute paths"]
        parsed_roots.append(root)
    if output is None or not parsed_roots:
        return None, ["grant roots and output_root must be absolute paths"]
    resolved_roots = tuple(root.resolve() for root in parsed_roots)
    output_root = output.resolve()
    if any(_overlaps(output_root, root) for root in resolved_roots):
        return None, ["grant output_root overlaps an input root"]
    return Grant(output_root, resolved_roots), []


def command_reasons(command: str, grant: Grant, cwd: Path) -> list[str]:
    if any(mark in command for mark in OPAQUE) or "\n" in command:
        return ["command contains shell syntax the checker cannot see inside"]
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return ["command has unbalanced quotes"]
    if not tokens or any(token in OPERATORS or any(op in token for op in OPERATORS) for token in tokens):
        return ["command is more than one simple command"]
    program = Path(tokens[0])
    if not PYTHON_NAME.match(program.name) or (program.name == tokens[0] and tokens[0].startswith(".")):
        return ["command does not start with python"]
    if not program.is_absolute() and program.name != tokens[0]:
        return ["command does not start with python"]
    index = 1
    while index < len(tokens) and tokens[index] in PYTHON_NOVALUE:
        index += 1
    if index >= len(tokens) or tokens[index].startswith("-"):
        return ["command is not the case-qc script"]
    script = _resolve(tokens[index], cwd)
    if script != CASE_QC:
        return ["command is not the case-qc script"]
    return flag_reasons(tokens[index + 1 :], grant, cwd)


def flag_reasons(tokens: list[str], grant: Grant, cwd: Path) -> list[str]:
    seen: set[str] = set()
    paths: list[Path] = []
    globs: list[str] = []
    outdir: Path | None = None
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        if not flag.startswith("--") or flag == "--":
            return ["command has an argument that is not an allowed flag"]
        if flag == "--allow-partial":
            if flag in seen:
                return ["command repeats a flag"]
            seen.add(flag)
            index += 1
            continue
        if index + 1 >= len(tokens):
            return [f"{flag} needs a value"]
        if flag == "--keys":
            values = []
            index += 1
            while index < len(tokens) and not tokens[index].startswith("--"):
                values.append(tokens[index])
                index += 1
            if not values or any(KEY_NAME.match(value) is None for value in values):
                return ["--keys has a value that is not a summary vector"]
            seen.add(flag)
            continue
        value = tokens[index + 1]
        index += 2
        if flag in seen and flag not in REPEATABLE:
            return ["command repeats a flag"]
        seen.add(flag)
        if flag == "--outdir":
            outdir = _resolve(value, cwd)
            if not Path(value).is_absolute():
                return ["--outdir must be absolute"]
        elif flag in PATH_FLAGS:
            if not Path(value).is_absolute():
                return [f"{flag} must be an absolute path"]
            paths.append(_resolve(value, cwd))
        elif flag in GLOB_FLAGS:
            globs.append(value)
        elif flag == "--expected":
            if not _bounded_int(value, 1, 100_000):
                return ["--expected is not an allowed integer"]
        elif flag == "--timeout":
            if not _bounded_int(value, 1, 900):
                return ["--timeout is outside 1 to 900 seconds"]
        elif flag == "--realizations":
            if REALIZATIONS.match(value) is None:
                return ["--realizations is not an ERT realization spec"]
        elif flag == "--time-index":
            if value not in TIME_INDEX:
                return ["--time-index is not an allowed value"]
        else:
            return [f"unknown flag {flag}"]
    if outdir is None:
        return ["command has no --outdir"]
    if not seen & INPUT_FLAGS:
        return ["command has no input"]
    reasons = []
    if not _under(outdir, grant.output_root):
        reasons.append("--outdir is outside output_root")
    if any(_overlaps(outdir, root) for root in grant.roots):
        reasons.append("--outdir overlaps an input root")
    for path in paths:
        if not any(_under(path, root) for root in grant.roots):
            reasons.append("an input path is outside the grant")
    for pattern in globs:
        reason = glob_reason(pattern, grant.roots)
        if reason:
            reasons.append(reason)
    return reasons


def glob_reason(pattern: str, roots: tuple[Path, ...]) -> str | None:
    if not pattern.startswith("/") or ".." in Path(pattern).parts:
        return "a glob is relative or contains .."
    matches = [Path(item).resolve() for item in glob.glob(pattern)]
    if matches:
        if any(not any(_under(match, root) for root in roots) for match in matches):
            return "a glob matches a path outside the grant"
        return None
    prefix = re.split(r"[*?[]", pattern, maxsplit=1)[0]
    probe = Path(prefix)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not any(_under(probe, root) for root in roots):
        return "a glob is outside the grant"
    return None


def _absolute(value: str) -> Path | None:
    path = Path(value)
    if not value or not path.is_absolute():
        return None
    return path


def _resolve(value: str, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _overlaps(left: Path, right: Path) -> bool:
    return _under(left, right) or _under(right, left)


def _bounded_int(value: str, low: int, high: int) -> bool:
    if not value.isdigit():
        return False
    number = int(value)
    return low <= number <= high


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    parser.add_argument("--grant", type=Path, default=DEFAULT_GRANT)
    parser.add_argument("--today", help="YYYY-MM-DD. Tests use this. The default is today.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print("ask: --today is not YYYY-MM-DD", file=sys.stderr)
        return 2
    decision = classify(args.command, grant_path=args.grant, today=today, cwd=args.cwd)
    if decision.allowed:
        print("allow")
        return 0
    print("ask: " + "; ".join(decision.reasons), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
