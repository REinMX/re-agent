#!/usr/bin/env python3
"""Classify each keyword of a deck by OPM Flow support, for the target simulator of the site contract.

Status per keyword:
  flow-ok           Flow simulates it, and no used item breaks a Flow restriction.
  flow-partial      Flow reads it, but at least one explicit item value is outside what Flow supports.
  flow-unsupported  Flow lists the keyword as unsupported (critical: Flow stops; not critical: Flow ignores it).
  eclipse-only      The OPM parser does not know the keyword. It is ECLIPSE-only or a typo. Flow cannot read the deck.

The table is a pinned snapshot of OPM's validation lists (references/flow_keyword_support.json). It is not a test
of the simulator. For an ECLIPSE target the Flow statuses do not fail the target verdict. They mark what a Flow
test on the home box cannot run.

  deck_support.py --deck CASE.DATA --contract site.contract.yml [--profile company] [--out support.json]
  deck_support.py --deck CASE.DATA --target ECLIPSE100 [--box-flow]

Needs opm (opm.io), which is in Komodo. Reads the deck only. Exit: 0 pass or warn, 1 fail, 2 bad input.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "stage-contract" / "scripts"))
import contract_lib  # noqa: E402

TABLE = HERE.parent / "references" / "flow_keyword_support.json"
PINNED_COMMIT = "c4d038b83b707618a81a6b64bbb2419e5e67a281"
FLOW = "FLOW"
ECLIPSE = frozenset({"ECLIPSE100", "ECLIPSE300"})
TARGETS = frozenset({FLOW, "IX"}) | ECLIPSE
STATUSES = ("flow-ok", "flow-partial", "flow-unsupported", "eclipse-only")
KEYWORD_LINE = re.compile(r"^[A-Z][A-Z0-9_+\-]{0,7}$")
INCLUDE_RE = re.compile(r"^\s*INCLUDE\s*$", re.I)
ALLOWED_NODES = (
    ast.Expression, ast.Compare, ast.BoolOp, ast.And, ast.Or, ast.Name, ast.Load, ast.Constant, ast.Call,
    ast.UnaryOp, ast.USub, ast.LtE, ast.Lt, ast.GtE, ast.Gt, ast.Eq, ast.NotEq,
)  # fmt: skip


def load_table(path: Path = TABLE) -> dict[str, Any]:
    table = json.loads(path.read_text(encoding="utf-8"))
    commit = table.get("source", {}).get("commit")
    if commit != PINNED_COMMIT:
        raise ValueError(f"support table commit {commit} differs from the pin {PINNED_COMMIT}; refresh both")
    return table


def rule_ok(rule: dict[str, Any], value: Any) -> bool:
    if "allow" in rule:
        return value in rule["allow"]
    tree = ast.parse(rule["expr"], mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES) or (
            isinstance(node, ast.Name) and node.id not in {"v", "len"}
        ):
            raise ValueError(f"unsafe rule expression: {rule['expr']}")
    return bool(eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, {"v": value, "len": len}))  # noqa: S307


def item_value(item: Any, kind: str) -> tuple[bool, Any]:
    """(explicit, value). Defaulted or empty items are not explicit; OPM skips them too."""
    if not getattr(item, "__has_value")(0) or getattr(item, "__defaulted")(0):
        return False, None
    if kind == "string":
        return True, item.get_str(0).strip("'\"") if item.is_string() else None
    if item.is_uda() if hasattr(item, "is_uda") else False:
        return True, None  # a UDQ name: OPM cannot check it either
    return True, item.get_int(0) if kind == "int" and item.is_int() else item.get_raw(0)


def parse_deck(deck: Path):
    import opm.io as opm_io
    from opm.io.parser import ParseContext, Parser

    ignore = opm_io.action.ignore
    context = ParseContext(
        [
            ("PARSE_UNKNOWN_KEYWORD", ignore),
            ("PARSE_RANDOM_TEXT", ignore),
            ("PARSE_RANDOM_SLASH", ignore),
            ("PARSE_MISSING_DIMS_KEYWORD", ignore),
            ("PARSE_EXTRA_RECORDS", ignore),
            ("SUMMARY_UNKNOWN_WELL", ignore),
            ("SUMMARY_UNKNOWN_GROUP", ignore),
        ]
    )
    return Parser().parse(str(deck), context)


def opm_knows(name: str, cache: dict[str, bool]) -> bool:
    if name not in cache:
        import opm.io as opm_io
        from opm.io.parser import ParseContext, Parser

        throw, ignore = opm_io.action.throw, opm_io.action.ignore
        context = ParseContext(
            [
                ("PARSE_UNKNOWN_KEYWORD", throw),
                ("PARSE_RANDOM_TEXT", ignore),
                ("PARSE_RANDOM_SLASH", ignore),
                ("PARSE_MISSING_DIMS_KEYWORD", ignore),
                ("PARSE_EXTRA_RECORDS", ignore),
            ]
        )
        try:
            Parser().parse_string(f"{name}\n/\n", context)
            cache[name] = True
        except Exception as error:  # noqa: BLE001 - opm raises RuntimeError or ValueError
            cache[name] = "Unknown keyword" not in str(error)
    return cache[name]


def takes_no_data(name: str, cache: dict[str, bool]) -> bool:
    """True for a keyword like OIL or ECHO, so the next line can be a keyword. TITLE and WOPR take a data line."""
    if name not in cache:
        try:
            parsed = list(parse_string(f"{name}\n"))
        except Exception:  # noqa: BLE001 - opm raises RuntimeError or ValueError
            parsed = None
        # An unknown keyword parses to an empty deck: its data size is unknown, so keep the next line a candidate.
        cache[name] = parsed is not None and (not parsed or len(parsed[0]) == 0)
    return cache[name]


def parse_string(text: str):
    import opm.io as opm_io
    from opm.io.parser import ParseContext, Parser

    ignore = opm_io.action.ignore
    names = ("PARSE_UNKNOWN_KEYWORD", "PARSE_RANDOM_TEXT", "PARSE_RANDOM_SLASH", "PARSE_MISSING_DIMS_KEYWORD")
    return Parser().parse_string(text, ParseContext([(n, ignore) for n in names]))


def deck_files(deck: Path) -> tuple[list[Path], list[str]]:
    """The DATA file and its INCLUDE files, in order. PATHS aliases are expanded. Missing files are notes."""
    files: list[Path] = []
    notes: list[str] = []
    aliases: dict[str, str] = {}

    def walk(path: Path) -> None:
        if path in files:
            return
        files.append(path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].split("--", 1)[0].strip()
            if line.upper() == "PATHS":
                i += 1
                while i < len(lines) and lines[i].split("--", 1)[0].strip() != "/":
                    found = re.findall(r"'([^']*)'|(\S+)", lines[i].split("--", 1)[0])
                    tokens = [a or b for a, b in found if (a or b) != "/"]
                    if len(tokens) >= 2:
                        aliases[tokens[0]] = tokens[1]
                    i += 1
            elif INCLUDE_RE.match(line):
                j = i + 1
                while j < len(lines) and not lines[j].split("--", 1)[0].strip():
                    j += 1
                if j < len(lines):
                    match = re.match(r"\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s/]+))", lines[j])
                    if match:
                        rel = next(g for g in match.groups() if g)
                        for alias, target in aliases.items():
                            rel = rel.replace(f"${alias}", target)
                        candidates = [deck.parent / rel, path.parent / rel]
                        hit = next((c for c in candidates if c.is_file()), None)
                        if hit is None:
                            notes.append(f"INCLUDE not found: {rel} (in {path.name})")
                        else:
                            walk(hit.resolve())
                i = j
            i += 1

    walk(deck.resolve())
    return files, notes


def keyword_lines(files: list[Path], root: Path) -> list[tuple[str, str]]:
    """(token, location) for each line that stands alone where a keyword can start."""
    out = []
    no_data: dict[str, bool] = {}
    for path in files:
        previous = ""
        for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            line = raw.split("--", 1)[0].strip()
            if not line:
                continue
            starts = (
                not previous
                or previous.endswith("/")
                or (bool(KEYWORD_LINE.match(previous)) and takes_no_data(previous, no_data))
            )
            if starts and KEYWORD_LINE.match(line):
                try:
                    where = path.relative_to(root)
                except ValueError:
                    where = Path(path.name)
                out.append((line, f"{where}:{number}"))
            previous = line
    return out


def classify(deck: Path, table: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    parsed = parse_deck(deck)
    files, notes = deck_files(deck)
    locations: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    cache: dict[str, bool] = {}
    for token, where in keyword_lines(files, deck.parent.resolve()):
        locations.setdefault(token, where)
    seen: dict[str, dict[str, Any]] = {}
    for keyword in parsed:
        name = keyword.name
        row = seen.setdefault(name, {"name": name, "count": 0, "violations": []})
        row["count"] += 1
        rules = table["partially_supported"].get(name)
        if not rules:
            continue
        for record_index in range(len(keyword)):
            record = keyword[record_index]
            for item_index in range(len(record)):
                rule = rules.get(str(item_index + 1))
                if rule is None:
                    continue
                explicit, value = item_value(record[item_index], rule["type"])
                if explicit and value is not None and not rule_ok(rule, value):
                    row["violations"].append(
                        {
                            "occurrence": row["count"],
                            "record": record_index + 1,
                            "item": item_index + 1,
                            "value": value,
                            "critical": rule["critical"],
                            "message": rule["message"],
                        }
                    )
    for token, where in keyword_lines(files, deck.parent.resolve()):
        if token not in seen and not opm_knows(token, cache):
            unknown.setdefault(token, []).append(where)
    rows = []
    for name, row in seen.items():
        entry: dict[str, Any] = {"name": name, "count": row["count"], "first_location": locations.get(name)}
        if name in table["unsupported"]:
            info = table["unsupported"][name]
            message = info["message"] or "Flow lists this keyword as unsupported"
            entry.update(status="flow-unsupported", critical=info["critical"], message=message)
        elif row["violations"]:
            entry.update(
                status="flow-partial",
                critical=any(v["critical"] for v in row["violations"]),
                violations=row["violations"],
            )
        else:
            entry.update(status="flow-ok", critical=False)
            if name in table["partially_supported"]:
                entry["note"] = "partially supported keyword; the used items are within Flow's support"
        rows.append(entry)
    for name, where in unknown.items():
        rows.append(
            {
                "name": name,
                "count": len(where),
                "first_location": where[0],
                "status": "eclipse-only",
                "critical": True,
                "message": "the OPM parser does not know this keyword. It is ECLIPSE-only or a typo.",
            }
        )
    return rows, notes


def verdicts(rows: list[dict[str, Any]], target: str) -> dict[str, str]:
    flagged = [r for r in rows if r["status"] != "flow-ok"]
    box = "FAIL" if any(r["critical"] for r in flagged) else ("WARN" if flagged else "PASS")
    if target == FLOW:
        return {"box_flow": box, "target": box}
    if target in ECLIPSE:
        return {"box_flow": box, "target": "PASS"}
    return {"box_flow": box, "target": "NOT_ASSESSED"}


def resolve_target(args: argparse.Namespace) -> tuple[str, str]:
    if args.target:
        return args.target.upper(), "--target"
    contract = contract_lib.load_or_exit(args.contract, args.profile, require_project=False)
    name = str((contract.get("simulator") or {}).get("name") or "").strip().upper()
    if not name:
        raise ValueError(
            f"simulator.name is empty in contract profile {contract.profile_name!r}; give --target"
        )
    return name, f"contract profile {contract.profile_name}"


def report_text(result: dict[str, Any]) -> str:
    lines = [
        f"deck: {result['deck']}",
        f"target simulator: {result['target_simulator']} ({result['target_source']})",
        f"support table: OPM opm-simulators {result['table_commit'][:7]}",
        f"verdict target: {result['verdicts']['target']}; box Flow test: {result['verdicts']['box_flow']}",
        "counts: " + ", ".join(f"{s} {result['counts'][s]}" for s in STATUSES),
    ]
    for row in result["keywords"]:
        if row["status"] == "flow-ok":
            continue
        tag = "critical" if row["critical"] else "not critical"
        lines.append(
            f"  {row['status']:<16} {row['name']:<9} x{row['count']} {tag} at {row['first_location']}"
        )
        if row.get("message"):
            lines.append(f"      {row['message']}")
        for v in row.get("violations", []):
            lines.append(
                f"      occurrence {v['occurrence']} record {v['record']} item {v['item']} = {v['value']!r}: {v['message']}"
            )
    box = result["verdicts"]["box_flow"]
    if result["target_simulator"] in ECLIPSE and box != "PASS":
        lines.append(
            "A box Flow test of this deck needs the critical keywords removed."
            if box == "FAIL"
            else "Flow ignores the flagged keywords, which are not critical."
        )
        lines.append("The table says what Flow rejects, not what ECLIPSE accepts.")
    for note in result["notes"]:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--deck", type=Path, required=True)
    contract_lib.add_contract_args(parser)
    parser.add_argument("--target", help=f"override simulator.name: one of {sorted(TARGETS)}")
    parser.add_argument(
        "--box-flow", action="store_true", help="exit 1 when a box Flow test of the deck fails"
    )
    parser.add_argument("--out", type=Path, help="write the JSON result here")
    args = parser.parse_args(argv)
    try:
        if not args.deck.is_file():
            raise ValueError(f"deck not found: {args.deck}")
        target, source = resolve_target(args)
        if target not in TARGETS:
            raise ValueError(f"simulator {target!r} is not one of {sorted(TARGETS)}")
        table = load_table()
        rows, notes = classify(args.deck, table)
    except ImportError as error:
        print(f"error: opm.io is needed ({error})", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    rows.sort(key=lambda r: (STATUSES.index(r["status"]), r["name"]))
    result = {
        "schema_version": 1,
        "deck": args.deck.name,
        "target_simulator": target,
        "target_source": source,
        "table_commit": table["source"]["commit"],
        "counts": {s: sum(r["status"] == s for r in rows) for s in STATUSES},
        "verdicts": verdicts(rows, target),
        "keywords": rows,
        "notes": notes,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(report_text(result))
    failed = result["verdicts"]["box_flow" if args.box_flow else "target"] == "FAIL"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
