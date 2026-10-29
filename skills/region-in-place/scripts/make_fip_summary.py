#!/usr/bin/env python3
"""Write a SUMMARY include with field and region in-place vectors (ECLIPSE 100 and OPM Flow).

The region sets come from the deck REGIONS section (FIPNUM plus every FIP<name> array,
also when made with EQUALS/COPY) or from --region-sets. All volumes are at surface
(standard) conditions. Nothing is run; the script only reads text.

Examples:
  make_fip_summary.py --deck <RUNPATH>/eclipse/model/<DECK>.DATA -o <PROJECT>/eclipse/include/summary/fip_regions.summary
  make_fip_summary.py --region-sets FIPNUM,FIPSEG,FIPRES --families all --with-pressure -o fip.summary

Exit codes: 0 ok, 2 bad input (no region sets, clashing 3-character suffixes, bad names).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fip_keywords as fk  # noqa: E402

SECTIONS = {"RUNSPEC", "GRID", "EDIT", "PROPS", "REGIONS", "SOLUTION", "SUMMARY", "SCHEDULE", "OPTIMIZE"}
MULTI_RECORD = {"EQUALS", "COPY", "ADD", "MULTIPLY", "MAXVALUE", "MINVALUE", "OPERATE", "OPERATER", "PATHS"}
KW_RE = re.compile(r"^[A-Z][A-Z0-9_+-]{0,7}$")
FOLLOW_SECTIONS = {None, "RUNSPEC", "REGIONS"}


class Deck:
    """Minimal reader: RUNSPEC dimensions and REGIONS FIP arrays. Not a full parser."""

    def __init__(self, data_file: Path, follow_all: bool = False, scan_values: bool = True):
        self.root = data_file.resolve().parent
        self.follow_all = follow_all
        self.scan_values = scan_values
        self.paths: dict[str, str] = {}
        self.section: str | None = None
        self.sets: dict[str, dict] = {}  # name -> {"max": int|None, "how": str}
        self.dims: dict[str, list[str]] = {}
        self.notes: list[str] = []
        self.skipped_includes: list[str] = []
        self._read(data_file.resolve())

    # -- tokenising -------------------------------------------------------
    @staticmethod
    def _clean(line: str) -> str:
        out, q = [], None
        i = 0
        while i < len(line):
            c = line[i]
            if q:
                out.append(c)
                if c == q:
                    q = None
            elif c in "'\"":
                q = c
                out.append(c)
            elif line.startswith("--", i):
                break
            else:
                out.append(c)
            i += 1
        return "".join(out).rstrip()

    @staticmethod
    def _split(line: str) -> tuple[list[str], bool]:
        """Tokens of one cleaned line and whether an unquoted '/' ended the record."""
        toks, cur, q = [], "", None
        for c in line:
            if q:
                cur += c
                if c == q:
                    q = None
            elif c in "'\"":
                q = c
                cur += c
            elif c == "/":
                if cur:
                    toks.append(cur)
                return toks, True
            elif c.isspace():
                if cur:
                    toks.append(cur)
                cur = ""
            else:
                cur += c
        if cur:
            toks.append(cur)
        return toks, False

    def _records(self, lines, i):
        """Collect tokens until an unquoted '/' starting at lines[i]. Returns (tokens, next_i)."""
        toks: list[str] = []
        while i < len(lines):
            line = self._clean(lines[i])
            i += 1
            if not line.strip():
                continue
            t, done = self._split(line)
            toks.extend(t)
            if done:
                return toks, i
        return toks, i

    def _include_path(self, raw: str) -> Path:
        p = raw.strip().strip("'\"")
        for alias, val in self.paths.items():
            p = p.replace("$" + alias, val)
        path = Path(p)
        return path if path.is_absolute() else self.root / path

    @staticmethod
    def _expand_max(tokens: list[str]) -> int | None:
        best = None
        for t in tokens:
            v = t.split("*", 1)[1] if "*" in t else t
            if not v:
                continue
            try:
                n = int(float(v))
            except ValueError:
                return None
            best = n if best is None else max(best, n)
        return best

    def _set(self, name: str, mx, how: str):
        name = name.upper().strip("'\"")
        if name in fk.NOT_REGION_SETS:
            return
        cur = self.sets.get(name)
        if cur is None:
            self.sets[name] = {"max": mx, "how": how}
            return
        if cur["max"] is None or mx is None:
            cur["max"] = None if how not in ("EQUALS",) or cur["max"] is None else max(cur["max"], mx or 0)
        else:
            cur["max"] = max(cur["max"], mx)
        cur["how"] += "+" + how

    # -- walking ----------------------------------------------------------
    def _read(self, path: Path):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError as exc:
            raise SystemExit(f"error: cannot read {path}: {exc}") from exc
        i = 0
        while i < len(lines):
            line = self._clean(lines[i])
            i += 1
            s = line.strip()
            if (
                not s
                or line[:1] in (" ", "\t", "'", '"', "/")
                and not KW_RE.match(s.split()[0] if s.split() else "")
            ):
                continue
            kw = s.split()[0].upper()
            if not KW_RE.match(kw):
                continue
            if kw in SECTIONS:
                self.section = kw
                continue
            if kw == "END":
                return
            if kw == "INCLUDE":
                toks, i = self._records(lines, i)
                if not toks:
                    continue
                inc = self._include_path(toks[0])
                if self.follow_all or self.section in FOLLOW_SECTIONS:
                    if inc.is_file():
                        self._read(inc)
                    else:
                        self.notes.append(f"include not found: {inc}")
                else:
                    self.skipped_includes.append(str(inc))
                continue
            if kw == "PATHS":
                while i < len(lines):
                    toks, i = self._records(lines, i)
                    if not toks:
                        break
                    if len(toks) >= 2:
                        self.paths[toks[0].strip("'\"")] = toks[1].strip("'\"")
                continue
            if self.section == "RUNSPEC" and kw in ("TABDIMS", "REGDIMS", "DIMENS"):
                toks, i = self._records(lines, i)
                self.dims[kw] = toks
                continue
            if self.section != "REGIONS":
                continue
            if kw in MULTI_RECORD:
                while i < len(lines):
                    toks, i = self._records(lines, i)
                    if not toks:
                        break
                    arr = toks[0].upper().strip("'\"")
                    if kw == "EQUALS" and (arr == "FIPNUM" or fk.SET_NAME_RE.match(arr)):
                        try:
                            val = int(float(toks[1]))
                        except (IndexError, ValueError):
                            val = None
                        self._set(arr, val, "EQUALS")
                    elif kw == "COPY" and len(toks) > 1:
                        dst = toks[1].upper().strip("'\"")
                        if dst == "FIPNUM" or fk.SET_NAME_RE.match(dst):
                            src = self.sets.get(arr, {}).get("max")
                            self._set(dst, src, "COPY")
                    elif arr == "FIPNUM" or fk.SET_NAME_RE.match(arr):
                        self._set(arr, None, kw)  # ADD/MULTIPLY/...: max unknown
                continue
            if kw == "FIPNUM" or (
                fk.SET_NAME_RE.match(kw) and kw not in fk.NOT_REGION_SETS and kw != "FIPOWG"
            ):
                toks, i = self._records(lines, i)
                mx = self._expand_max(toks) if self.scan_values else None
                self._set(kw, mx, "array")
                continue
            if kw == "FIPOWG":
                self.sets.setdefault("FIPOWG", {"max": 3, "how": "FIPOWG (generated oil/gas/water zones)"})
                continue


def build_include(sets: list[str], families: list[str], with_pressure: bool, source: str) -> str:
    lines = [
        f"-- Generated by region-in-place/make_fip_summary.py on {dt.date.today().isoformat()}",
        f"-- Source: {source}",
        "-- Works in ECLIPSE 100 and OPM Flow. In-place volumes are at SURFACE (standard) conditions:",
        "--   METRIC: sm3 (oil, gas, water). FIELD: stb (oil, water), Mscf (gas).",
        "-- Region keyword rule: R<family> for FIPNUM; for FIP<xxx> pad R<family> to 5 chars with '_'",
        "--   and add the first 3 chars after FIP (FIPSEG -> ROIP_SEG, ROIPLSEG). The FIP arrays must",
        "--   exist in REGIONS, otherwise OPM Flow ignores the keyword (warning); check the ECLIPSE PRT too.",
        "-- An empty record '/' means all regions of the set.",
        "",
        "-- Field totals",
    ]
    for fam in families:
        lines.append(f"{fk.field_keyword(fam):<9}-- {fk.FAMILIES[fam]}")
    if with_pressure:
        lines.append("FPR      -- field average pressure (hydrocarbon pore-volume weighted)")
    for s in sets:
        lines += [
            "",
            f"-- Region set {s}" + (" (built-in)" if s == "FIPNUM" else f" -> suffix {fk.set_suffix(s)}"),
        ]
        for fam in families:
            lines += [fk.region_keyword(fam, s), "/"]
        if with_pressure:
            lines += [fk.region_keyword(fk.PRESSURE_FAMILY, s), "/"]
    lines += [
        "",
        "-- Optional PRT fallback (put in the right sections, not here):",
        "--   SOLUTION: RPTSOL   'FIP=3' /   SCHEDULE: RPTSCHED 'FIP=3' /",
        "--   FIP=2 prints field + FIPNUM balance reports, FIP=3 also the FIP<xxx> sets.",
        "",
    ]
    return "\n".join(lines)


def dims_advice(deck: Deck | None, sets: dict[str, dict]) -> dict:
    known = [v["max"] for v in sets.values() if v.get("max") is not None]
    unknown = [k for k, v in sets.items() if v.get("max") is None]
    n_extra = sum(1 for k in sets if k != "FIPNUM")
    out: dict = {
        "max_region_id": max(known) if known else None,
        "sets_with_unknown_max": unknown,
        "n_extra_sets": n_extra,
        "warnings": [],
    }
    if out["max_region_id"] is not None:
        ntfip = out["max_region_id"]
        out["advice"] = (
            f"REGDIMS item 1 NTFIP >= {ntfip}; item 2 NMFIPR >= {ntfip + n_extra} "
            f"(NTFIP + number of FIP<xxx> sets; also covers the 'number of sets' reading)"
        )
    if deck is None:
        return out

    def item(kw, n):
        toks = deck.dims.get(kw) or []
        flat = []
        for t in toks:
            if "*" in t:
                c, v = t.split("*", 1)
                flat += [v or None] * int(c or 1)
            else:
                flat.append(t)
        try:
            return int(flat[n - 1]) if len(flat) >= n and flat[n - 1] else None
        except ValueError:
            return None

    ntfip_deck = max([v for v in (item("TABDIMS", 5), item("REGDIMS", 1)) if v is not None], default=1)
    nmfipr_deck = item("REGDIMS", 2) or 1
    out["deck"] = {
        "TABDIMS_NTFIP": item("TABDIMS", 5),
        "REGDIMS_NTFIP": item("REGDIMS", 1),
        "REGDIMS_NMFIPR": item("REGDIMS", 2),
    }
    if out["max_region_id"] is not None and ntfip_deck < out["max_region_id"]:
        out["warnings"].append(
            f"NTFIP in deck is {ntfip_deck} but the largest region id is {out['max_region_id']}: "
            "ECLIPSE 100 needs NTFIP >= the largest region id; raise TABDIMS item 5 or REGDIMS item 1"
        )
    if n_extra and nmfipr_deck < 1 + n_extra:
        out["warnings"].append(
            f"REGDIMS item 2 NMFIPR is {nmfipr_deck} but there are {n_extra} FIP<xxx> sets: set REGDIMS"
        )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--deck", type=Path, help="<DECK>.DATA; REGIONS section is scanned (INCLUDEs followed)")
    src.add_argument("--region-sets", help="comma list, e.g. FIPNUM,FIPSEG,FIPRES")
    ap.add_argument(
        "--families", default="all", help="OIP,GIP,WIP,OIPL,OIPG,GIPL,GIPG or 'all' (default all)"
    )
    ap.add_argument("--with-pressure", action="store_true", help="also FPR and RPR/RPR__<xxx>")
    ap.add_argument("--no-fipnum", action="store_true", help="leave out the built-in FIPNUM set")
    ap.add_argument("--follow-all-includes", action="store_true", help="also read INCLUDEs in GRID/PROPS/...")
    ap.add_argument("--no-scan-values", action="store_true", help="do not read array values (huge decks)")
    ap.add_argument("-o", "--output", type=Path, help="include file to write (default: stdout)")
    ap.add_argument("--json", type=Path, help="write the region-set report as JSON")
    a = ap.parse_args(argv)
    try:
        families = fk.parse_families(a.families)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    deck = None
    if a.deck:
        if not a.deck.is_file():
            print(f"error: deck not found: {a.deck}", file=sys.stderr)
            return 2
        deck = Deck(a.deck, a.follow_all_includes, not a.no_scan_values)
        sets = dict(deck.sets)
        source = f"REGIONS of {a.deck.name}"
        if "FIPNUM" not in sets:
            sets = {"FIPNUM": {"max": 1, "how": "default (FIPNUM absent = one region)"}, **sets}
    else:
        sets = {}
        for s in a.region_sets.split(","):
            s = s.strip().upper()
            if not s:
                continue
            if s != "FIPNUM" and not fk.SET_NAME_RE.match(s):
                print(f"error: {s!r} is not FIPNUM or FIP<1-5 chars>", file=sys.stderr)
                return 2
            sets[s] = {"max": None, "how": "--region-sets"}
        source = f"--region-sets {a.region_sets}"
    if a.no_fipnum:
        sets.pop("FIPNUM", None)
    if not sets:
        print("error: no region sets", file=sys.stderr)
        return 2
    problems = fk.check_unique_suffixes(sets)
    if problems:
        print("error: " + "; ".join(problems), file=sys.stderr)
        return 2
    order = (["FIPNUM"] if "FIPNUM" in sets else []) + sorted(s for s in sets if s != "FIPNUM")
    text = build_include(order, families, a.with_pressure, source)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text)
    else:
        sys.stdout.write(text)
    report = {
        "region_sets": {
            s: {
                **sets[s],
                "summary_suffix": fk.set_suffix(s),
                "keywords": [fk.region_keyword(f, s) for f in families],
            }
            for s in order
        },
        "families": families,
        "dims": dims_advice(deck, sets),
        "notes": deck.notes if deck else [],
        "skipped_includes": deck.skipped_includes if deck else [],
    }
    if a.json:
        a.json.write_text(json.dumps(report, indent=2))
    for s in order:
        mx = sets[s]["max"]
        print(
            f"region set {s:<8} max id {mx if mx is not None else '?':>4}  -> {', '.join(report['region_sets'][s]['keywords'][:3])} ...",
            file=sys.stderr,
        )
    d = report["dims"]
    if d.get("advice"):
        print("dims: " + d["advice"], file=sys.stderr)
    for w in d["warnings"]:
        print("WARNING: " + w, file=sys.stderr)
    for n in report["notes"]:
        print("note: " + n, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
