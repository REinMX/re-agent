#!/usr/bin/env python3
"""Build a deck fingerprint for the SCHEDULE keywords that control wells, groups, gas lift, networks,
actions, reservoir coupling, VFP tables and timing.

Normalizes Eclipse/Flow include text (strip comments, collapse whitespace), expands SCHEDULE includes
(nested too), extracts tracked keyword blocks, and writes deck_fingerprint.json. A block ends at the next
keyword line; an ACTIONX block runs to ENDACTIO, so a change inside an action belongs to ACTIONX. When the deck
has START, each block carries the date it applies from, so moving a block to another report date is a change.

Does not run Flow/ERT. Does not invent SUMMARY vectors.

Usage:
  deck_fingerprint.py --data /path/to/<ECLBASE>.DATA --out deck_fingerprint.json
  deck_fingerprint.py --contract site.contract.yml --runpath <realization-N/iter-M> --out fp.json
      (deck name from contract eclbase, e.g. eclipse/model/<DECK>; no default;
       --eclbase overrides)
  deck_fingerprint.py --data ... --out ... --json   # also print to stdout

Exit: 0 ok, 2 bad input, 1 unexpected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402

SCHEMA_VERSION = 3

# Tracked SCHEDULE keywords by family. NETWORK (RUNSPEC dimensions) is outside the SCHEDULE scope; the
# extended network shows through BRANPROP and NODEPROP.
KEYWORD_FAMILIES: dict[str, tuple[str, ...]] = {
    "wells": ("WELSPECS", "COMPDAT", "WELSEGS", "COMPSEGS", "WELOPEN", "WPIMULT", "WTEST"),
    "well_controls": ("WCONPROD", "WCONINJE", "WCONHIST", "WELTARG", "WECON", "WCYCLE"),
    "groups": ("GRUPTREE", "GCONPROD", "GCONINJE", "GCONSALE", "GCONSUMP", "GUIDERAT", "WGRUPCON"),
    "gas_lift": ("LIFTOPT", "WLIFTOPT", "GLIFTOPT"),
    "network": ("GRUPNET", "NETBALAN", "BRANPROP", "NODEPROP"),
    "vfp": ("VFPPROD", "VFPINJ"),
    "actions": ("ACTIONX", "UDQ"),
    "coupling": ("GRUPMAST", "GRUPSLAV", "SLAVES"),
    "timing": ("TSTEP", "DATES"),
}
TRACKED_KEYWORDS = tuple(k for keys in KEYWORD_FAMILIES.values() for k in keys)
FAMILY_OF = {k: family for family, keys in KEYWORD_FAMILIES.items() for k in keys}
# VFP tables can be large, so the fingerprint stores their hash and table numbers, not their text.
HASH_ONLY = frozenset({"VFPPROD", "VFPINJ"})
WELL_FIRST_ITEM = (
    "WELSPECS",
    "COMPDAT",
    "WCONPROD",
    "WCONINJE",
    "WCONHIST",
    "WELTARG",
    "WLIFTOPT",
    "WGRUPCON",
)
WELL_FIRST_ITEM += ("WELOPEN", "WTEST", "WCYCLE")
KEYWORD_LINE_RE = re.compile(r"^[A-Z][A-Z0-9_+\-]{0,7}$")

INCLUDE_PATH_RE = re.compile(
    r"INCLUDE\s*\n\s*(?:'([^']+)'|\"([^\"]+)\"|(\S+))\s*/",
    re.IGNORECASE,
)
SECTION_RE = re.compile(
    r"^\s*(RUNSPEC|GRID|EDIT|PROPS|REGIONS|SOLUTION|SUMMARY|SCHEDULE|OPTIMIZE)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COMMENT_RE = re.compile(r"--[^\n]*")
START_RE = re.compile(r"^\s*START\s*\n\s*(\d+)\s+'?([A-Za-z]{3})'?\s+(\d{4})", re.MULTILINE | re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)} | {
    "JLY": 7
}


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"[ \t]+", " ", line)
        lines.append(line)
    return "\n".join(lines)


def resolve_includes(data_file: Path) -> tuple[str, list[str]]:
    """Expand INCLUDE under SCHEDULE (and any root-level schedule includes).

    Returns (concatenated_schedule_text, list of source paths used).
    """
    root_text = data_file.read_text(encoding="utf-8", errors="replace")
    sources: list[str] = [str(data_file.resolve())]
    base = data_file.parent

    # Find SCHEDULE section body in the DATA file.
    sections = list(SECTION_RE.finditer(root_text))
    schedule_body = ""
    for i, m in enumerate(sections):
        if m.group(1).upper() == "SCHEDULE":
            start = m.end()
            end = sections[i + 1].start() if i + 1 < len(sections) else len(root_text)
            # Stop at END keyword if present
            end_m = re.search(r"^\s*END\s*$", root_text[start:end], re.MULTILINE | re.IGNORECASE)
            if end_m:
                end = start + end_m.start()
            schedule_body = root_text[start:end]
            break
    if not schedule_body.strip():
        # Fallback: whole file (some decks are schedule-only includes)
        schedule_body = root_text

    return expand_includes(strip_comments(schedule_body), base, data_file, sources, set()), sources


def deck_start(data_file: Path) -> date | None:
    """START of the deck (includes before SCHEDULE expanded), or None when it is not found.

    A missing include (for example one that ERT writes per realization) is skipped, so START in a later
    include that exists is still found.
    """
    text = strip_comments(data_file.read_text(encoding="utf-8", errors="replace"))
    sched = next((m for m in SECTION_RE.finditer(text) if m.group(1).upper() == "SCHEDULE"), None)
    head = text[: sched.start()] if sched else text
    try:
        head = expand_includes(head, data_file.parent, data_file, [], set(), missing=[])
    except FileNotFoundError:  # include cycle
        pass
    match = START_RE.search(head)
    if not match or match.group(2).upper() not in MONTHS:
        return None
    return date(int(match.group(3)), MONTHS[match.group(2).upper()], int(match.group(1)))


def _date_record(line: str) -> date | None:
    tokens = line.replace("'", " ").replace('"', " ").replace("/", " ").split()
    if len(tokens) < 3 or tokens[1].upper() not in MONTHS:
        return None
    try:
        return date(int(tokens[2]), MONTHS[tokens[1].upper()], int(tokens[0]))
    except ValueError:
        return None


def _tstep_days(line: str) -> float:
    days = 0.0
    for token in line.replace("/", " ").split():
        count, _, value = token.rpartition("*")
        try:
            days += float(value.replace("D", "E").replace("d", "e")) * (int(count) if count else 1)
        except ValueError:
            continue
    return days


PATHS_RE = re.compile(r"^\s*PATHS\s*$(.*?)^\s*/\s*$", re.IGNORECASE | re.MULTILINE | re.DOTALL)


def path_aliases(text: str) -> dict[str, str]:
    """PATHS aliases of a deck (RUNSPEC): {alias: folder}. INCLUDE '$ALIAS/file' uses them."""
    aliases: dict[str, str] = {}
    for block in PATHS_RE.finditer(strip_comments(text)):
        for line in block.group(1).splitlines():
            tokens = [a or b for a, b in re.findall(r"'([^']*)'|([^\s/']+)", line)]
            if len(tokens) >= 2:
                aliases[tokens[0]] = tokens[1]
    return aliases


def expand_includes(
    text: str,
    base: Path,
    where: Path,
    sources: list[str],
    stack: set[Path],
    aliases: dict[str, str] | None = None,
    missing: list[str] | None = None,
) -> str:
    """Replace each INCLUDE statement with the file text, recursively. Paths are relative to the DATA folder.

    PATHS aliases ($ALIAS) come from the DATA file (`where` at the top level) unless given.
    A missing include raises FileNotFoundError, unless `missing` is a list: then it is recorded there and skipped.
    """
    if aliases is None:
        aliases = path_aliases(where.read_text(encoding="utf-8", errors="replace")) if where.is_file() else {}

    def replace(match: re.Match) -> str:
        rel = next(g for g in match.groups() if g).strip()
        # Whole alias names only: with MOD and MOD05 defined, $MOD05 must not match MOD.
        rel = re.sub(r"\$(\w+)", lambda m: aliases.get(m.group(1), m.group(0)), rel)
        inc_path = next((p.resolve() for p in (base / rel, where.parent / rel) if p.is_file()), None)
        if inc_path is None:
            if missing is not None:
                missing.append(rel)
                return "\n"
            raise FileNotFoundError(f"INCLUDE not found: {rel} (from {where})")
        if inc_path in stack:
            raise FileNotFoundError(f"INCLUDE cycle: {rel} (from {where})")
        sources.append(str(inc_path))
        inner = strip_comments(inc_path.read_text(encoding="utf-8", errors="replace"))
        return (
            "\n"
            + expand_includes(inner, base, inc_path, sources, stack | {inc_path}, aliases, missing)
            + "\n"
        )

    return INCLUDE_PATH_RE.sub(replace, text)


def extract_keyword_blocks(text: str, start: date | None = None) -> dict[str, str]:
    """Return {KEYWORD: normalized block text} for tracked keywords.

    A block ends at the next keyword line. ACTIONX runs to ENDACTIO and keeps the keywords inside it.
    With start, each block starts with the line "@<date>" of the report date it applies from.
    """
    lines = normalize_ws(strip_comments(text)).split("\n")
    when: datetime | None = datetime(start.year, start.month, start.day) if start else None
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    timing: str | None = None
    buf: list[str] = []
    tracked = set(TRACKED_KEYWORDS)

    def flush() -> None:
        nonlocal current, buf
        if current is not None:
            blocks.setdefault(current, []).append("\n".join(buf).strip())
        current, buf = None, []

    for line in lines:
        token = line.upper()
        if current == "ACTIONX":
            buf.append(line)
            if token == "ENDACTIO":
                flush()
            continue
        if KEYWORD_LINE_RE.match(token):
            flush()
            if token in tracked:
                current, buf = token, ([f"@{when.date().isoformat()}"] if when else []) + [token]
            timing = token if token in {"DATES", "TSTEP"} else None
            continue
        if when is not None and timing == "DATES":
            day = _date_record(line)
            if day is not None:
                when = datetime(day.year, day.month, day.day)
        elif when is not None and timing == "TSTEP":
            when += timedelta(days=_tstep_days(line))
        if current is not None:
            buf.append(line)
    flush()
    return {key: "\n".join(p for p in parts if p).strip() for key, parts in blocks.items()}


def table_ids(body: str) -> list[int]:
    """The table number (item 1 of record 1) of each VFP table in a block."""
    ids = []
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.upper() in HASH_ONLY and i + 1 < len(lines):
            first = lines[i + 1].split()[0] if lines[i + 1].split() else ""
            if first.isdigit():
                ids.append(int(first))
    return sorted(set(ids))


def wells_from_blocks(blocks: dict[str, str]) -> list[str]:
    wells: set[str] = set()
    for key in WELL_FIRST_ITEM:
        body = blocks.get(key, "")
        for line in body.split("\n"):
            if not line or line.upper() == key:
                continue
            # item 1 is the well name; templates (OP*), lists (*L) and defaults are not wells
            match = re.match(r"\s*(?:'([^']*)'|\"([^\"]*)\"|(\S+))", line)
            tok = next((g for g in match.groups() if g is not None), "").strip() if match else ""
            if re.match(r"^[A-Za-z][A-Za-z0-9_\-]*$", tok):
                wells.add(tok.upper())
    return sorted(wells)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_fingerprint(data_file: Path) -> dict[str, Any]:
    raw, sources = resolve_includes(data_file)
    start = deck_start(data_file)
    blocks = extract_keyword_blocks(raw, start)
    keyword_meta: dict[str, Any] = {}
    concat_parts: list[str] = []
    for key in TRACKED_KEYWORDS:
        if key not in blocks:
            continue
        norm = blocks[key]
        keyword_meta[key] = {
            "family": FAMILY_OF[key],
            "normalized": None if key in HASH_ONLY else norm,
            "sha256": sha256_text(norm),
            "n_lines": len(norm.splitlines()),
        }
        if key in HASH_ONLY:
            keyword_meta[key]["table_ids"] = table_ids(norm)
        concat_parts.append(f"[{key}]\n{norm}")

    combined = "\n\n".join(concat_parts)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deck_root": str(data_file.parent.resolve()),
        "data_file": str(data_file.resolve()),
        "sources": sources,
        "tracked_keywords_present": sorted(keyword_meta.keys()),
        "keywords": keyword_meta,
        "combined_sha256": sha256_text(combined),
        "start": start.isoformat() if start else None,
        "wells_mentioned": wells_from_blocks(blocks),
        "notes": (
            "Fingerprint covers SCHEDULE keywords for wells, controls, groups, gas lift, networks, VFP tables, "
            "actions, coupling and timing (KEYWORD_FAMILIES). "
            "Rock/PVT/relperm GEN_KW includes are out of scope for scenario QC of wells/schedule."
        ),
    }


def fingerprint_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Diff two fingerprints. Returns changed keywords and well sets."""
    ka = a.get("keywords") or {}
    kb = b.get("keywords") or {}
    keys = sorted(set(ka) | set(kb))
    changed: list[str] = []
    detail: dict[str, Any] = {}
    for key in keys:
        ha = (ka.get(key) or {}).get("sha256")
        hb = (kb.get(key) or {}).get("sha256")
        if ha != hb:
            changed.append(key)
            detail[key] = {
                "baseline_sha256": ha,
                "scenario_sha256": hb,
                "baseline_present": key in ka,
                "scenario_present": key in kb,
            }
    wells_a = set(a.get("wells_mentioned") or [])
    wells_b = set(b.get("wells_mentioned") or [])
    return {
        "schema_equal": a.get("schema_version") == b.get("schema_version"),
        # Blocks carry report dates only when START resolved; one-sided dates make every block differ.
        "dates_comparable": (a.get("start") is None) == (b.get("start") is None),
        "combined_equal": a.get("combined_sha256") == b.get("combined_sha256"),
        "changed_keywords": changed,
        "changed_families": sorted({FAMILY_OF.get(k, "other") for k in changed}),
        "keyword_detail": detail,
        "wells_added": sorted(wells_b - wells_a),
        "wells_removed": sorted(wells_a - wells_b),
        "wells_shared": sorted(wells_a & wells_b),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deck fingerprint for scenario QC.")
    parser.add_argument("--data", type=Path, default=None, help="Path to .DATA file")
    parser.add_argument("--contract", type=Path, default=None, help="site.contract.yml (eclbase)")
    parser.add_argument("--profile", default=None, help="Contract profile (default: env RE_AGENT_PROFILE)")
    parser.add_argument("--runpath", type=Path, default=None, help="Runpath or case dir holding the deck")
    parser.add_argument("--eclbase", default=None, help="Override deck name / eclipse/model/NAME")
    parser.add_argument("--out", type=Path, required=True, help="Output deck_fingerprint.json")
    parser.add_argument("--json", action="store_true", help="Also print fingerprint JSON to stdout")
    args = parser.parse_args(argv)

    if args.data is None:
        if args.runpath is None:
            sys.stderr.write("error: give --data, or --runpath (+ --contract or --eclbase)\n")
            return 2
        contract: dict[str, Any] = {}
        try:
            if args.eclbase is None:
                contract = contract_lib.load_contract(args.contract, args.profile, require_project=False)
            sub, name = contract_lib.resolve_eclbase(contract, args.eclbase)
        except contract_lib.ContractError as exc:
            sys.stderr.write(f"error: contract: {exc}\n")
            return 2
        args.data = args.runpath / sub / f"{name}.DATA"

    if not args.data.is_file():
        sys.stderr.write(f"error: DATA file not found: {args.data}\n")
        return 2

    try:
        fp = build_fingerprint(args.data)
    except FileNotFoundError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: fingerprint failed: {exc}\n")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(
        f"fingerprint: ok keywords={fp['tracked_keywords_present']} "
        f"wells={fp['wells_mentioned']} sha={fp['combined_sha256'][:12]}…\n"
        f"wrote: {args.out}\n"
    )
    if args.json:
        json.dump(fp, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
