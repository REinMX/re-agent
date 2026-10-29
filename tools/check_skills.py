#!/usr/bin/env python3
"""Check the skills in this repository.

Checks: front matter, name equals folder name, description length, semver version,
file size, references, registry/router/feature-map entries, three distinct manual eval tasks,
harness links, VERSION equal to the pyproject version, changelog fragments, shared-line
changes, and the STE lint when explicitly requested with --ste-lint PATH.
These checks do not prove agent behavior.
Exit code 0 = pass, 1 = at least one failure.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
_spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release.py"))
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# Semantic Versioning 2.0.0, from semver.org.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
REF_RE = re.compile(r"`((?:references|scripts)/[\w./-]+)`")
MAX_BODY_LINES = 500
MAX_DESCRIPTION = 1024


def parse_front_matter(text: str) -> tuple[dict[str, str], str] | None:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, match.group(2)


def check_skill(folder: Path) -> list[str]:
    errors: list[str] = []
    skill_file = folder / "SKILL.md"
    if not skill_file.is_file():
        return [f"{folder.name}: SKILL.md is missing"]
    parsed = parse_front_matter(skill_file.read_text(encoding="utf-8"))
    if parsed is None:
        return [f"{folder.name}: front matter is missing or not closed"]
    fields, body = parsed
    name = fields.get("name", "")
    description = fields.get("description", "")
    if name != folder.name:
        errors.append(f"{folder.name}: name '{name}' differs from the folder name")
    if not NAME_RE.match(name):
        errors.append(f"{folder.name}: name '{name}' must use lowercase letters, digits, and hyphens")
    if not description:
        errors.append(f"{folder.name}: description is empty")
    if len(description) > MAX_DESCRIPTION:
        errors.append(
            f"{folder.name}: description has {len(description)} characters (limit {MAX_DESCRIPTION})"
        )
    version = fields.get("version", "")
    if not SEMVER_RE.match(version):
        errors.append(f"{folder.name}: version '{version}' must be a semantic version, for example 0.1.0")
    if len(body.splitlines()) > MAX_BODY_LINES:
        errors.append(f"{folder.name}: body has more than {MAX_BODY_LINES} lines")
    for ref in sorted(set(REF_RE.findall(body))):
        if not (folder / ref).exists():
            errors.append(f"{folder.name}: referenced file '{ref}' does not exist")
    return errors


def check_registration(name: str) -> list[str]:
    """Reject incomplete skill packaging. This is not a semantic policy checker."""
    errors: list[str] = []
    for relative, label in (
        ("AGENTS.md", "AGENTS.md"),
        ("skills/reservoir-engineer/SKILL.md", "router"),
        ("docs/FEATURE_MAP.md", "docs/FEATURE_MAP.md"),
    ):
        if label == "router" and name == "reservoir-engineer":
            continue
        path = ROOT / relative
        prefix = r"^\|[^|\n]*\|" if label == "router" else r"^\|"
        pattern = prefix + rf"[ \t]*`{re.escape(name)}`[ \t]*\|"
        if not path.is_file() or not re.search(pattern, path.read_text(), re.M):
            errors.append(f"{name}: {label} table entry is missing")
    path = ROOT / "evals" / f"{name}.md"
    if not path.is_file():
        errors.append(f"{name}: manual eval file is missing")
    else:
        tasks = {int(n) for n in re.findall(r"^## Task\s+(\d+)\.", path.read_text(), re.M)}
        if len(tasks) < 3:
            errors.append(f"{name}: manual eval needs at least three distinct tasks")
    return errors


def check_links() -> list[str]:
    errors = []
    for link in (ROOT / ".claude/skills", ROOT / ".agents/skills"):
        if link.is_file() and not link.is_symlink() and link.read_text().strip() == "../skills":
            print(
                f"note: {link.relative_to(ROOT)} is a Git symlink placeholder. Use install_project.py for real IDE discovery."
            )
            continue
        if not link.exists() or link.resolve() != SKILLS.resolve():
            errors.append(f"{link.relative_to(ROOT)} must resolve to skills/")
    return errors


def check_version(root: Path) -> list[str]:
    """VERSION must be a semantic version and equal the pyproject version."""
    try:
        version = (root / "VERSION").read_text().strip()
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        return [f"version: cannot read VERSION or pyproject.toml ({error})"]
    errors = []
    if not SEMVER_RE.match(version):
        errors.append(f"VERSION '{version}' is not a semantic version")
    if version != project:
        errors.append(f"VERSION '{version}' differs from the pyproject version '{project}'")
    return errors


def run_ste_lint(lint: Path) -> list[str]:
    if not lint.is_file():
        return [f"ste-lint: requested path is not a file: {lint}"]
    files = []
    for folder in source_folders():
        for path in [folder / "SKILL.md", *(folder / "references").rglob("*.md")]:
            if path.is_file() and not any(
                part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(folder)
            ):
                files.append(str(path))
    try:
        lint = lint.resolve()
        digest = hashlib.sha256(lint.read_bytes()).hexdigest()
        print(f"ste-lint: tool={lint} sha256={digest} python={sys.executable}")
        result = subprocess.run(
            [sys.executable, str(lint), *sorted(files)], capture_output=True, text=True, check=False
        )
    except OSError as error:
        return [f"ste-lint: cannot run requested tool {lint}: {error}"]
    if result.returncode != 0:
        return [f"ste-lint: exit {result.returncode}\n{result.stdout.strip()}\n{result.stderr.strip()}"]
    return []


def source_folders() -> list[Path]:
    # Bytecode left by another branch is not skill source. A partial source skill still fails.
    return sorted(
        p
        for p in SKILLS.iterdir()
        if p.is_dir()
        and not p.is_symlink()
        and any(f.is_file() and "__pycache__" not in f.parts for f in p.rglob("*"))
    )


def check_fragments(root: Path) -> list[str]:
    errors = []
    for path in release.fragment_paths(root):
        try:
            release.parse_fragment(path, root)
        except (OSError, ValueError) as error:
            errors.append(f"{path.relative_to(root)}: {error}")
    return errors


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def check_shared_lines(root: Path, base: str | None = None) -> list[str]:
    if base is None:
        result = git(root, "merge-base", "HEAD", "origin/main")
        base = result.stdout.strip() if result.returncode == 0 else None
    if not base:
        print("shared-line check skipped: no base ref")
        return []
    diff = git(root, "diff", "--name-only", base, "--")
    if diff.returncode:
        return [f"shared-line check: {diff.stderr.strip()}"]
    version = root / "VERSION"
    old_version = git(root, "show", f"{base}:VERSION")
    if (
        old_version.returncode == 0
        and version.is_file()
        and version.read_text().strip() != old_version.stdout.strip()
        and not release.fragment_paths(root)
    ):
        return []
    changed = set(diff.stdout.splitlines())
    changed.update(git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines())
    errors = []
    if "CHANGELOG.md" in diff.stdout.splitlines():
        errors.append("CHANGELOG.md: add changes/<topic>.md instead; `make release` writes CHANGELOG.md")
    bumped = set()
    removed = ""
    for path in release.fragment_paths(root):
        try:
            sections, bumps = release.parse_fragment(path, root)
            bumped.update(bumps)
            removed += "\n".join(sections.get("Removed", [])) + "\n"
        except (OSError, ValueError):
            pass
    names = {
        path.parts[1]
        for value in changed
        if len((path := Path(value)).parts) >= 3 and path.parts[0] == "skills"
    }
    for name in sorted(names):
        folder = root / "skills" / name
        if folder.is_symlink():
            continue
        old = git(root, "show", f"{base}:skills/{name}/SKILL.md")
        if old.returncode != 0:
            continue
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file() and f"`{name}`" in removed:
            continue  # a deleted skill has nothing to bump. A ### Removed bullet names it
        before = parse_front_matter(old.stdout)
        after = parse_front_matter(skill_file.read_text()) if skill_file.is_file() else None
        if (before[0].get("version") if before else None) != (after[0].get("version") if after else None):
            errors.append(
                f"{name}: version: changes only in `make release`; add `bump: {name} <level>` to a fragment"
            )
        if name not in bumped and any(
            path.startswith(f"skills/{name}/") and "__pycache__" not in Path(path).parts for path in changed
        ):
            errors.append(f"{name}: changed without a fragment that bumps it")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--ste-lint", type=Path, help="explicit path to the optional STE Python linter")
    args = parser.parse_args([] if argv is None else argv)
    errors: list[str] = []
    folders = source_folders()
    if not folders:
        errors.append("no skills found")
    for folder in folders:
        errors.extend(check_skill(folder))
        errors.extend(check_registration(folder.name))
    for folder in sorted((ROOT / "plugins/pstack/skills").glob("*")):
        if folder.is_dir():
            errors.extend(check_skill(folder))
    for link in SKILLS.iterdir():
        if link.is_symlink():
            expected = ROOT / "plugins/pstack/skills" / link.name.removeprefix("pstack-")
            if (
                not link.name.startswith("pstack-")
                or not expected.is_dir()
                or link.resolve() != expected.resolve()
            ):
                errors.append(f"{link.name}: unexpected skill symlink")
    errors.extend(check_links())
    errors.extend(check_version(ROOT))
    errors.extend(check_fragments(ROOT))
    errors.extend(check_shared_lines(ROOT, args.base))
    if args.ste_lint is not None:
        errors.extend(run_ste_lint(args.ste_lint))
    for error in errors:
        print(f"FAIL: {error}")
    print(f"{len(folders)} skills checked, {len(errors)} failures")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
