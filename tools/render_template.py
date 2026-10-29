#!/usr/bin/env python3
"""Render a template folder into a new output folder.

Tokens:
  {{site:<dotted.key>}}   a value from the selected profile of the site contract (contract_lib rules:
                          --contract, RE_AGENT_CONTRACT, ./site.contract.yml, the user file;
                          --profile, RE_AGENT_PROFILE, active_profile)
  {{param:<name>}}        --set name=value, else the default in <template>/params.yml

A token without a value is an error: the script lists every one with file:line and writes nothing.
An empty value, a null, or an <ASK ...> placeholder is a missing value.
Lists and mappings render as JSON, which YAML and Python read.
The output folder must not exist or must be empty, and must be outside this repository and the template.
Files that are not UTF-8 text are copied unchanged. File modes are kept, and a file that starts with #!
becomes executable (an installed copy may have lost its mode). params.yml is not copied.

Exit code: 0 = rendered, 1 = missing values, 2 = usage, contract, or path error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/stage-contract/scripts"))

import contract_lib  # noqa: E402

PARAMS_FILE = "params.yml"
TOKEN_RE = re.compile(r"\{\{(.*?)\}\}")
NAME_RE = re.compile(r"^\s*(site|param):([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*$")
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


class RenderError(Exception):
    pass


class MissingValues(RenderError):
    def __init__(self, items: list[str]) -> None:
        super().__init__(f"{len(items)} token(s) without a value")
        self.items = items


def text_value(value):
    if value is None or (isinstance(value, str | list | dict) and len(value) == 0):
        return None
    if contract_lib.has_placeholder(value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict):
        return json.dumps(value, default=str)
    return str(value)


def read_params(template: Path, sets: list[str]) -> dict[str, str | None]:
    path = template / PARAMS_FILE
    try:
        data = contract_lib.read_yaml(path) if path.is_file() else {}
    except contract_lib.ContractError as exc:
        raise RenderError(str(exc)) from exc
    values = {str(k): text_value(v) for k, v in data.items()}
    for item in sets:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise RenderError(f"--set needs name=value, got {item!r}")
        if name.strip() not in values:
            raise RenderError(f"--set {name.strip()}: not declared in {path}")
        values[name.strip()] = text_value(value)  # empty or <ASK ...> is a missing value, as in params.yml
    return values


def template_files(template: Path) -> list[Path]:
    files = []
    for path in sorted(template.rglob("*")):
        rel = path.relative_to(template)
        if any(part in SKIP_PARTS for part in rel.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise RenderError(f"{rel.as_posix()}: symlinks are not supported in templates")
        if path.is_file() and rel.as_posix() != PARAMS_FILE:
            files.append(path)
    return files


def decode(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_output(output: Path, template: Path) -> Path:
    out = output.expanduser().resolve()
    for parent, label in ((ROOT, "this repository"), (template, "the template folder")):
        if out == parent or out.is_relative_to(parent):
            raise RenderError(f"output {out} is inside {label}; choose a folder outside it")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise RenderError(f"output {out} exists and is not an empty folder")
    return out


def render(template: Path, output: Path, sets: list[str], contract=None, profile=None) -> list[Path]:
    template = template.expanduser().resolve()
    if not template.is_dir():
        raise RenderError(f"template folder not found: {template}")
    out = check_output(output, template)
    params = read_params(template, sets)
    files = template_files(template)
    loaded = {}

    def site(key: str):
        if "contract" not in loaded:
            try:
                loaded["contract"] = contract_lib.load_contract(contract, profile, require_project=False)
            except contract_lib.ContractError as exc:
                raise RenderError(f"contract: {exc}") from exc
        return text_value(contract_lib.get_dotted(loaded["contract"], key))

    rendered: dict[Path, bytes] = {}
    missing: list[str] = []
    for path in files:
        rel = path.relative_to(template).as_posix()
        data = path.read_bytes()
        text = decode(data)
        if text is None:
            rendered[path] = data
            continue
        lines = text.splitlines(keepends=True)
        for number, line in enumerate(lines, 1):

            def substitute(match: re.Match, number=number, rel=rel) -> str:
                parsed = NAME_RE.match(match.group(1))
                if not parsed:
                    missing.append(f"{rel}:{number}: unknown token {match.group(0)}")
                    return match.group(0)
                kind, key = parsed.groups()
                if kind == "site":
                    value = site(key)
                elif key not in params:
                    missing.append(f"{rel}:{number}: param:{key} is not declared in {PARAMS_FILE}")
                    return match.group(0)
                else:
                    value = params[key]
                if value is None:
                    missing.append(f"{rel}:{number}: {kind}:{key} has no value")
                    return match.group(0)
                return value

            lines[number - 1] = TOKEN_RE.sub(substitute, line)
        rendered[path] = "".join(lines).encode("utf-8")
    if missing:
        raise MissingValues(missing)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for path, data in rendered.items():
        target = out / path.relative_to(template)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        shutil.copymode(path, target)
        if data.startswith(b"#!"):
            os.chmod(target, target.stat().st_mode | 0o111)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "template", type=Path, help="template folder, for example templates/site/01-fmu-prior-design"
    )
    parser.add_argument("output", type=Path, help="new or empty folder outside this repository")
    contract_lib.add_contract_args(parser)
    parser.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE", help="param value; repeat"
    )
    args = parser.parse_args(argv)
    try:
        written = render(args.template, args.output, args.set, args.contract, args.profile)
    except MissingValues as exc:
        print(f"ERROR: {exc}. Nothing was written.", file=sys.stderr)
        for item in exc.items:
            print(f"  {item}", file=sys.stderr)
        return 1
    except (RenderError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"rendered {len(written)} file(s) into {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
