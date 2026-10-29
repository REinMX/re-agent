#!/usr/bin/env python3
"""Fold changelog fragments and update release versions."""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
LEVELS = ("patch", "minor", "major")
SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)


def version_key(value: str) -> tuple:
    match = SEMVER.fullmatch(value)
    if not match:
        raise ValueError(f"invalid semantic version: {value}")
    pre = match[4]
    identifiers = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ()
    return (*map(int, match.group(1, 2, 3)), pre is None, identifiers)


def fragment_paths(root: Path) -> list[Path]:
    return sorted(p for p in (root / "changes").glob("*.md") if p.name != "README.md")


def parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    section = None
    block: list[str] = []

    def flush(trim: bool = True) -> None:
        if block:
            text = "".join(block)
            sections[section].append(text.rstrip("\n") if trim else text.removesuffix("\n"))
            block.clear()

    for line in text.splitlines(keepends=True):
        if line.startswith("### "):
            flush()
            section = line.strip()[4:]
            if section not in SECTIONS:
                raise ValueError(f"unknown section: {section}")
            sections.setdefault(section, [])
        elif re.match(r"^[-*+]\s+\S", line):
            if section is None:
                raise ValueError("fragment text outside a known ### section")
            flush(trim=False)
            block.append(line)
        elif not line.strip():
            if block:
                block.append(line)
        elif section is not None and block and line.startswith((" ", "\t")):
            block.append(line)
        else:
            raise ValueError("fragment text outside a known ### section or bullet")
    flush()
    if not any(sections.values()):
        raise ValueError("fragment without bullets")
    if any(not bullets for bullets in sections.values()):
        raise ValueError("section without bullets")
    return sections


def parse_fragment(path: Path, root: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    bumps = {}
    if text.startswith("---\n"):
        match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.S)
        if not match:
            raise ValueError("unclosed fragment front matter")
        for line in match[1].splitlines():
            if not line.startswith("bump:"):
                raise ValueError("front matter must contain bump: skill level")
            for item in line[5:].split(","):
                parts = item.split()
                if len(parts) != 2:
                    raise ValueError(f"invalid bump: {item.strip()}")
                skill, level = parts
                if level not in LEVELS:
                    raise ValueError(f"bad level: {level}")
                if (
                    not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill)
                    or not (root / "skills" / skill / "SKILL.md").is_file()
                ):
                    raise ValueError(f"unknown skill: {skill}")
                if skill not in bumps or LEVELS.index(level) > LEVELS.index(bumps[skill]):
                    bumps[skill] = level
        text = text[match.end() :]
    return parse_sections(text), bumps


def skill_version(text: str) -> re.Match:
    front = re.match(r"\A---\n.*?\n---(?:\n|$)", text, re.S)
    match = re.search(r"^version:[ \t]*(\S+)[ \t]*$", front[0], re.M) if front else None
    if match is None:
        raise ValueError("missing skill version front matter")
    version_key(match[1])
    return match


def prepare_release(root: Path, version: str, date: str) -> tuple[dict[Path, str], list[Path], str]:
    if version_key(version) <= version_key((root / "VERSION").read_text().strip()):
        raise ValueError("--version must be greater than current VERSION")
    if datetime.date.fromisoformat(date).isoformat() != date:
        raise ValueError("--date must be an ISO date (YYYY-MM-DD)")
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text()
    header = re.search(r"^## \[Unreleased\][ \t]*\n", text, re.M)
    if header is None:
        raise ValueError("CHANGELOG.md: missing ## [Unreleased]")
    older = re.search(r"^## ", text[header.end() :], re.M)
    end = header.end() + older.start() if older else len(text)
    legacy = text[header.end() : end]
    paths = fragment_paths(root)
    if not paths and not legacy.strip():
        raise ValueError("no fragments and empty Unreleased")
    merged = parse_sections(legacy) if legacy.strip() else {}
    bumps: dict[str, str] = {}
    for path in paths:
        sections, requested = parse_fragment(path, root)
        for section, bullets in sections.items():
            merged.setdefault(section, []).extend(bullets)
        for skill, level in requested.items():
            if skill not in bumps or LEVELS.index(level) > LEVELS.index(bumps[skill]):
                bumps[skill] = level
    section = f"## [{version}] - {date}\n\n"
    section += (
        "\n\n".join(f"### {name}\n\n" + "\n".join(merged[name]) for name in SECTIONS if merged.get(name))
        + "\n\n"
    )
    writes = {changelog: text[: header.end()] + "\n" + section + text[end:]}
    report = section
    for skill, level in sorted(bumps.items()):
        path = root / "skills" / skill / "SKILL.md"
        text = path.read_text()
        match = skill_version(text)
        numbers = list(version_key(match[1])[:3])
        index = 2 - LEVELS.index(level)
        numbers[index] += 1
        numbers[index + 1 :] = [0] * (2 - index)
        new = ".".join(map(str, numbers))
        writes[path] = text[: match.start(1)] + new + text[match.end(1) :]
        report += f"skills/{skill}/SKILL.md: {match[1]} -> {new}\n"
    project_path = root / "pyproject.toml"
    project = project_path.read_text()
    table = re.search(r"^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", project, re.M | re.S)
    match = re.search(r'^(version[ \t]*=[ \t]*)([\'"])([^\n]*?)\2', table[1], re.M) if table else None
    if match is None:
        raise ValueError("pyproject.toml: missing [project] version")
    start = table.start(1) + match.start(3)
    end = table.start(1) + match.end(3)
    writes[project_path] = project[:start] + version + project[end:]
    writes[root / "VERSION"] = version + "\n"
    report += f"VERSION, pyproject.toml: -> {version}\n"
    return writes, paths, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        writes, paths, report = prepare_release(args.root, args.version, args.date)
        if not args.dry_run:
            for path, text in writes.items():
                path.write_text(text, encoding="utf-8")
            for path in paths:
                path.unlink()
        print(report, end="")
    except (OSError, ValueError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
