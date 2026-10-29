"""The public repository names no company and holds no personal paths.

The terms are rot13 in tests/data/public_hygiene_terms.json, so this file and the data file do not match.
Allowed: CHANGELOG.md and LICENSE, links to the public open-source repositories of the org
(github.com/<org>/..., <org>/<repo>, <org>.github.io), and lines that carry a Copyright attribution.
"""

import codecs
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
TERMS = json.loads((ROOT / "tests/data/public_hygiene_terms.json").read_text(encoding="utf-8"))
EXEMPT_FILES = {"CHANGELOG.md", "LICENSE"}


def decode(term: str) -> str:
    return codecs.decode(term, "rot13")


ORG = re.escape(decode(TERMS["org"]))
FORBIDDEN = [decode(t) for t in (*TERMS["company"], *TERMS["domains"], *TERMS["personal_paths"])]
ALLOWED_LINKS = re.compile(
    rf"github\.com/{ORG}/|{ORG}\.github\.io|(?<![\w/.-]){ORG}/[\w.-]+",
    re.I,
)


def tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=30
        )
        return [name for name in result.stdout.decode().split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        names = []
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d != ".git"]
            names += [(Path(folder) / f).relative_to(ROOT).as_posix() for f in files]
        return names


def hits(name: str, text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "copyright" in line.lower():
            continue
        line = ALLOWED_LINKS.sub("", line).lower()
        found += [f"{name}:{number}: {term}" for term in FORBIDDEN if term in line]
    return found


def test_terms_decode_to_the_guarded_strings():
    assert decode(TERMS["org"]) in FORBIDDEN
    assert all(t.startswith("/") for t in map(decode, TERMS["personal_paths"]))


def test_allowed_forms_are_stripped_and_plain_mentions_are_not():
    org = decode(TERMS["org"])
    assert hits("a.md", f"See https://github.com/{org}/fmu-drogon and `{org}/res2df`.") == []
    assert hits("a.md", f"https://{org}.github.io/everest-tutorials/x.html") == []
    assert hits("a.md", f"(Copyright {org.title()} and OPM contributors)") == []
    assert hits("a.md", f"Run it at {org.title()}.") == [f"a.md:1: {org}"]
    assert hits("a.md", f"templates/{org}/01-x") == [f"a.md:1: {org}"]
    assert hits("a.md", f"x\nPY={decode(TERMS['personal_paths'][0])}/x") == [
        f"a.md:2: {decode(TERMS['personal_paths'][0])}"
    ]


def test_tracked_files_hold_no_company_names_or_personal_paths():
    found = []
    for name in tracked_files():
        path = ROOT / name
        if name in EXEMPT_FILES or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        found += hits(name, data.decode("utf-8", errors="replace"))
    assert not found, "public hygiene: remove or neutralise\n" + "\n".join(found)
