#!/usr/bin/env python3
"""Make the pinned Flow keyword-support table from OPM's validation sources.

Reads UnsupportedFlowKeywords.cpp and PartiallySupportedFlowKeywords.cpp of one opm-simulators commit and writes
references/flow_keyword_support.json. Run it only to refresh the pin; deck_support.py reads the JSON.

  flow_keyword_table.py --src <dir with the two .cpp files> --commit <full sha> --out references/flow_keyword_support.json

Get the files with:
  curl -fLO https://raw.githubusercontent.com/OPM/opm-simulators/<sha>/opm/simulators/utils/UnsupportedFlowKeywords.cpp
  curl -fLO https://raw.githubusercontent.com/OPM/opm-simulators/<sha>/opm/simulators/utils/PartiallySupportedFlowKeywords.cpp

Exit: 0 written, 2 bad input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = "https://github.com/OPM/opm-simulators"
FILES = {
    "unsupported": "opm/simulators/utils/UnsupportedFlowKeywords.cpp",
    "partial": "opm/simulators/utils/PartiallySupportedFlowKeywords.cpp",
}
LICENSE_NOTE = (
    "Facts extracted from OPM opm-simulators source files that are GPL-3.0-or-later (Copyright Equinor and OPM "
    "contributors): keyword names, item numbers, allowed values and messages. The C++ files are not copied here."
)
UNSUPPORTED_RE = re.compile(
    r'\{\s*"([A-Z0-9_+\-]+)"\s*,\s*\{\s*(true|false)\s*,\s*(?:std::nullopt|std::string\s*\{\s*"((?:[^"\\]|\\.)*)"\s*\})\s*\}\s*\}'
)
BLOCK_RE = re.compile(r"static const (?:KeywordValidation::)?SupportedKeywordItems<(std::string|int|double)>")
KEYWORD_RE = re.compile(r'\{\s*"([A-Z0-9_+\-]+)"\s*,\s*\{')
ITEM_RE = re.compile(
    r'\{\s*(\d+)\s*,\s*\{\s*(true|false)\s*,\s*(.*?)\s*,\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\}\s*\}', re.S
)
LITERAL_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
ALLOW_RE = re.compile(r"^allow_values<[^>]+>\s*\{(.*)\}$", re.S)
LAMBDA_RE = re.compile(
    r"^\[\]\s*\(\s*(?:const\s+)?[\w:]+\s*&?\s*(\w+)\s*\)\s*\{\s*return\s+(.*?);\s*\}$", re.S
)
TYPES = {"std::string": "string", "int": "int", "double": "double"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_cpp_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def parse_unsupported(text: str) -> dict[str, dict]:
    out = {}
    for name, critical, message in UNSUPPORTED_RE.findall(strip_cpp_comments(text)):
        out[name] = {"critical": critical == "true", "message": message or None}
    return out


def parse_rule(text: str, kind: str) -> dict:
    text = " ".join(text.split())
    allow = ALLOW_RE.match(text)
    if allow:
        body = allow.group(1).strip()
        if kind == "string":
            values = re.findall(r'"([^"]*)"', body)
        else:
            values = [float(v) if kind == "double" else int(v) for v in body.split(",") if v.strip()]
        return {"allow": values}
    lam = LAMBDA_RE.match(text)
    if lam:
        var, expr = lam.groups()
        expr = re.sub(rf"\b{var}\.size\(\)", "len(v)", expr)
        expr = re.sub(rf"\b{var}\b", "v", expr).replace("&&", " and ").replace("||", " or ")
        return {"expr": " ".join(expr.split())}
    raise ValueError(f"unknown validator: {text[:80]}")


def parse_partial(text: str) -> dict[str, dict[str, dict]]:
    text = strip_cpp_comments(text)
    blocks = list(BLOCK_RE.finditer(text))
    if not blocks:
        raise ValueError("no SupportedKeywordItems blocks found")
    out: dict[str, dict[str, dict]] = {}
    for i, block in enumerate(blocks):
        kind = TYPES[block.group(1)]
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        body = text[block.end() : end]
        names = list(KEYWORD_RE.finditer(body))
        for j, name in enumerate(names):
            stop = names[j + 1].start() if j + 1 < len(names) else len(body)
            for item, critical, rule, message in ITEM_RE.findall(body[name.end() : stop]):
                message = "".join(LITERAL_RE.findall(message))
                entry = {
                    "type": kind,
                    "critical": critical == "true",
                    "message": message,
                    **parse_rule(rule, kind),
                }
                out.setdefault(name.group(1), {})[item] = entry
    return out


def build(src: Path, commit: str) -> dict:
    paths = {key: src / Path(rel).name for key, rel in FILES.items()}
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"missing source file: {path}")
    unsupported = parse_unsupported(paths["unsupported"].read_text())
    partial = parse_partial(paths["partial"].read_text())
    if not unsupported or not partial:
        raise ValueError("empty table: the source format changed")
    return {
        "schema_version": 1,
        "source": {
            "repository": REPO,
            "commit": commit,
            "files": {
                key: {"path": rel, "url": f"{REPO}/blob/{commit}/{rel}", "sha256": sha256(paths[key])}
                for key, rel in FILES.items()
            },
            "license_note": LICENSE_NOTE,
        },
        "item_numbering": "1-based, as in the ECLIPSE manual and OPM KeywordValidation",
        "rule_semantics": (
            "A rule applies only to an item with an explicit, non-defaulted value. "
            "allow: the value must be in the list (an empty list means the item must be defaulted). "
            "expr: a Python expression in v that must be true."
        ),
        "unsupported": dict(sorted(unsupported.items())),
        "partially_supported": {
            k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in sorted(partial.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--commit", required=True, help="full opm-simulators commit sha of the source files")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        print("error: --commit must be a full 40-character sha", file=sys.stderr)
        return 2
    try:
        table = build(args.src, args.commit)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out}: {len(table['unsupported'])} unsupported, "
        f"{len(table['partially_supported'])} partially supported keywords (commit {args.commit[:7]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
