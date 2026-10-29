#!/usr/bin/env python3
"""Prepare a reviewed copy of a deck and ERT SCAL wiring."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _record(text: str, keyword: str) -> re.Match:
    hits = list(re.finditer(rf"(?mi)^[ \t]*{keyword}\b[^\n]*\n", text))
    if len(hits) != 1:
        raise ValueError(f"Expected one inline {keyword} keyword, found {len(hits)}")
    return hits[0]


def deck_dimensions(text: str) -> tuple[int, int]:
    hit = _record(text, "TABDIMS")
    end = text.find("/", hit.end())
    if end < 0:
        raise ValueError("TABDIMS has no terminating slash")
    tokens = re.sub(r"--[^\n]*", "", text[hit.end() : end]).split()
    values = []
    for token in tokens:
        count, star, value = token.partition("*")
        if star:
            values.extend([int(value) if value else None] * int(count))
        else:
            values.append(int(token))
    values += [None] * max(0, 3 - len(values))
    return values[0] or 1, values[2] or 20


def rewrite_deck(text: str, rows: int, include: str = "relperm.inc") -> tuple[str, dict]:
    """Replace inline saturation tables and enlarge the declared table capacity."""
    if rows < 2 or "'" in include or "\n" in include:
        raise ValueError("Invalid table row count or include name")
    ntsfun, old_rows = deck_dimensions(text)
    replacements = []
    keywords = ["SWOF"]
    if re.search(r"(?mi)^\s*SGOF\b", text):
        keywords.append("SGOF")
    for keyword in keywords:
        hit = _record(text, keyword)
        end = hit.end()
        for _ in range(ntsfun):
            slash = re.search(r"/|--[^\n]*", text[end:])
            while slash and slash.group().startswith("--"):
                end += slash.end()
                slash = re.search(r"/|--[^\n]*", text[end:])
            if slash is None:
                raise ValueError(f"{keyword} contains fewer than {ntsfun} tables")
            body = re.sub(r"--[^\n]*", "", text[end : end + slash.start()])
            try:
                for value in body.replace(",", " ").split():
                    float(value.replace("D", "E").replace("d", "e"))
            except ValueError as error:
                raise ValueError(f"{keyword} contains fewer than {ntsfun} numeric tables") from error
            end += slash.end()
        replacement = f"INCLUDE\n '{include}' /\n" if keyword == "SWOF" else ""
        replacements.append((hit.start(), end, replacement))
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    hit = _record(text, "TABDIMS")
    end = text.index("/", hit.end())
    record = re.sub(r"--[^\n]*", "", text[hit.end() : end]).split()
    expanded = []
    for token in record:
        count, star, value = token.partition("*")
        expanded.extend([value or "1*"] * int(count) if star else [token])
    expanded += ["1*"] * max(0, 3 - len(expanded))
    expanded[2] = str(max(rows, old_rows))
    text = text[: hit.end()] + " " + " ".join(expanded) + " " + text[end:]
    return text, {
        "keywords": keywords,
        "satnums": ntsfun,
        "nssfun_before": old_rows,
        "nssfun_after": max(rows, old_rows),
    }


def parse_groups(spec: str) -> dict[str, list[int]]:
    """Accept rock:1,2;other:3,4 or rock:1,2,other:3,4."""
    groups = {}
    current = None
    for token in spec.replace(";", ",").split(","):
        if ":" in token:
            current, token = token.split(":", 1)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", current) or current in groups:
                raise ValueError("Group names must be unique identifiers")
            groups[current] = []
        if current is None or not token.isdigit() or int(token) < 1:
            raise ValueError("Use --groups name:1,2;other:3")
        groups[current].append(int(token))
    members = [number for group in groups.values() for number in group]
    if len(set(members)) != len(members):
        raise ValueError("A SATNUM cannot belong to multiple groups")
    return groups


def wire(
    rec: Path,
    deck: Path,
    ert: Path,
    groups: dict[str, list[int]],
    out: Path,
    prior: str = "uniform",
    delta_s: float = 0.01,
) -> dict:
    from scal_rec import generate_tables, load_recommendation

    # semeio's PYSCAL reads a scalar. pyscalcli explicitly rejects per-SATNUM lists.
    if len(groups) != 1:
        raise ValueError(
            "semeio PYSCAL and pyscal 0.18 support one interpolation group per "
            "recommendation, not per-SATNUM parameter lists. Split independent decks "
            "or use one shared facies group; multiple groups cannot be wired safely."
        )
    text = deck.read_text(encoding="latin-1")
    ntsfun, _ = deck_dimensions(text)
    if sorted(next(iter(groups.values()))) != list(range(1, ntsfun + 1)):
        raise ValueError("The group must cover every deck SATNUM exactly once")
    load_recommendation(rec, delta_s)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output directory must be empty or new")
    out.mkdir(parents=True, exist_ok=True)
    tables = generate_tables(rec, [-1.0, -0.5, 0.0, 0.5, 1.0], delta_s, out / "tables")
    if any(table["qc"]["returncode"] != 0 for table in tables):
        raise ValueError("Generated tables failed deck QC; inspect tables reports")
    rows = max(table["rows"] for entry in tables for table in entry["tables"])
    # A full [0,1] grid plus endpoint insertion nodes bounds random interpolation tables.
    capacity = max(rows, math.ceil(1 / delta_s) + 16)
    rewritten, report = rewrite_deck(text, capacity)
    for entry in tables:
        for keyword in report["keywords"]:
            found = {table["satnum"] for table in entry["tables"] if table["keyword"] == keyword}
            if found != set(range(1, ntsfun + 1)):
                raise ValueError(f"Recommendation {keyword} SATNUMs differ from TABDIMS NTSFUN")
    (out / deck.name).write_text(rewritten, encoding="latin-1")
    shutil.copyfile(rec, out / rec.name)
    base = next(item for item in tables if item["param"] == 0)
    shutil.copyfile(base["include"], out / "relperm.inc")
    group = next(iter(groups))
    names = [f"SCAL_WO_{group}"]
    if "SGOF" in report["keywords"]:
        names.append(f"SCAL_GO_{group}")
    distribution = "UNIFORM -1 1" if prior == "uniform" else "TRUNCATED_NORMAL 0 0.5 -1 1"
    (out / "scal.dist").write_text("".join(f"{name} {distribution}\n" for name in names))
    args = [
        f"<PARAMETER_FILE>={out.resolve() / rec.name}",
        "<RESULT_FILE>=model/relperm.inc",
        f"<INT_PARAM_WO_NAME>={names[0]}",
    ]
    if len(names) == 2:
        args.append(f"<INT_PARAM_GO_NAME>={names[1]}")
    line = "FORWARD_MODEL PYSCAL(" + ", ".join(args) + ")"
    snippet = f"GEN_KW SCAL {out.resolve() / 'scal.dist'}\n{line}\n"
    (out / "scal.ert.inc").write_text(snippet)
    config = ert.read_text()
    flow = re.search(r"(?m)^FORWARD_MODEL\s+FLOW\b", config)
    if flow is None:
        raise ValueError("ERT config must have a FORWARD_MODEL FLOW insertion point")
    config = config[: flow.start()] + snippet + config[flow.start() :]
    # Freeze the source config location before moving the copy.
    config = config.replace("<CONFIG_PATH>", str(ert.resolve().parent))
    token = re.compile(r"[^\s()=,\"']+\.DATA", re.IGNORECASE)
    candidates = [hit for hit in token.finditer(config) if hit.group().endswith(deck.name)]
    if len(candidates) != 1:
        raise ValueError(
            "Cannot locate a unique input deck token in ERT config. "
            "Use the emitted scal.ert.inc and deck copy for explicit integration."
        )
    hit = candidates[0]
    config = config[: hit.start()] + str(out.resolve() / deck.name) + config[hit.end() :]
    # Rebase source config paths, preserving runtime output arguments.
    source_keys = {
        "INCLUDE": 1,
        "INSTALL_JOB": 2,
        "LOAD_WORKFLOW_JOB": 1,
        "LOAD_WORKFLOW": 1,
        "OBS_CONFIG": 1,
        "GEN_KW": 2,
        "REFCASE": 1,
        "ENSPATH": 1,
        "RUNPATH": 1,
    }
    rewritten_lines = []
    for raw in config.splitlines():
        words = raw.split()
        index = source_keys.get(words[0]) if words else None
        if words and words[0] == "DEFINE" and len(words) >= 3:
            if (ert.resolve().parent / words[2]).exists():
                index = 2
        if index is not None and len(words) > index:
            value = words[index]
            if not value.startswith(("/", "<")):
                words[index] = str(ert.resolve().parent / value)
                raw = " ".join(words)
        rewritten_lines.append(raw)
    config = "\n".join(rewritten_lines) + "\n"
    (out / ert.name).write_text(config)
    report.update(
        {
            "groups": groups,
            "parameters": names,
            "forward_model": line,
            "distribution": distribution,
            "deck": str(out / deck.name),
            "ert": str(out / ert.name),
            "row_count": rows,
        }
    )
    (out / "wire.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("rec", "deck", "ert", "out"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--prior", choices=("uniform", "truncnorm"), default="uniform")
    parser.add_argument("--delta-s", type=float, default=0.01)
    args = parser.parse_args(argv)
    try:
        result = wire(
            args.rec, args.deck, args.ert, parse_groups(args.groups), args.out, args.prior, args.delta_s
        )
    except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"- inline {', '.join(result['keywords'])}; NSSFUN {result['nssfun_before']}")
    print(f"+ INCLUDE 'relperm.inc'; NSSFUN {result['nssfun_after']}")
    print(result["forward_model"])
    print(f"Review copies under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
