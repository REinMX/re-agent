#!/usr/bin/env python3
"""Check the property tables of a reservoir simulator deck. Read-only.

It reads saturation functions (relative permeability and capillary pressure), PVT tables,
EQUIL, and VFP tables with res2df. It applies rules that find tables that break a simulator
or that are physically wrong. It does not run a simulator.

Two modes:
    deck_qc.py --deck CASE.DATA                    the whole deck with its INCLUDE files
    deck_qc.py --include a.inc [--include b.inc]   table include files only (text mode)

Use text mode when the OPM parser cannot read the whole deck, for example when it has the
IMPORT keyword. Text mode cannot see the unit system of the deck.

Rules (E = error, W = warning, I = information):
    L001 E  the deck or an include cannot be read
    I001 E  an INCLUDE or IMPORT file does not exist (found by a text scan, no parser)
    S001 E  saturation values are not strictly increasing
    S002 E  a saturation or a relative permeability is outside 0 to 1
    S003 E  krw or krg decreases, or kro decreases with water or gas saturation
    S004 W  capillary pressure is not monotone
    S005 W  krw at the first water saturation is not 0
    S006 I  SWOF does not end at Sw = 1 (SPE9 is such a deck, and it runs)
    S007 W  SGOF does not start at Sg = 0
    S008 W  kro at connate water differs between SWOF and SGOF
    S009 W  maximum gas saturation plus connate water is larger than 1
    S010 W  saturation table rows exceed TABDIMS NSSFUN
    P005 W  rock compressibility is zero
    P001 E  pressure, volume factor, viscosity or density is not positive, or compressibility is negative
    P002 E  pressure or Rs is not increasing where the keyword requires it
    P003 W  oil volume factor, viscosity, or saturated pressure has the wrong trend
    P004 W  gas volume factor does not decrease with pressure
    E001 E  EQUIL datum pressure is not positive
    E002 E  the gas-oil contact is deeper than the oil-water contact
    V001 E  a VFP table has a value that is not finite or not positive
    V002 W  BHP falls when THP rises at fixed rate and fractions
    V003 W  VFP datum depth is 0 or missing
    V004 W  VFP unit system differs from the deck
    T001 I  a table family has no table
    T002 E  a keyword has more tables than TABDIMS NTSFUN (saturation) or NTPVT (PVT)
    T003 W  a PVT table has more rows or records than TABDIMS NPPVT or NRPVT (saturation rows are S010)
Exit code: 0 = no errors, 1 = errors, 2 = usage error.
Thresholds are options. They are starting points.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from res2df import ResdataFiles, equil, pvt, satfunc
from res2df import vfp as vfp_module

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schedule_qc import check_opm_deck  # noqa: E402

# keyword -> (saturation column, {column: trend}, capillary pressure column)
# trend "inc" = non-decreasing, "dec" = non-increasing. Both are with increasing saturation.
SAT_SPEC = {
    "SWOF": ("SW", {"KRW": "inc", "KROW": "dec"}, "PCOW"),
    "SWFN": ("SW", {"KRW": "inc"}, "PCOW"),
    "SGOF": ("SG", {"KRG": "inc", "KROG": "dec"}, "PCOG"),
    "SGFN": ("SG", {"KRG": "inc"}, "PCOG"),
    "SOF3": ("SO", {"KROW": "inc", "KROG": "inc"}, None),
    "SOF2": ("SO", {"KRO": "inc"}, None),
}
UNIT_KEYWORDS = ("METRIC", "FIELD", "LAB", "PVT-M")


class Findings:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, code: str, severity: str, where: str, message: str) -> None:
        self.rows.append({"code": code, "severity": severity, "where": where, "message": message})


def decreasing(values: np.ndarray, tol: float = 1e-12) -> int:
    return int(np.sum(np.diff(values) < -tol))


def increasing(values: np.ndarray, tol: float = 1e-12) -> int:
    return int(np.sum(np.diff(values) > tol))


def check_satfunc_rows(df: pd.DataFrame, limit: int, found: Findings) -> None:
    for (keyword, satnum), table in df.groupby(["KEYWORD", "SATNUM"]):
        if len(table) > limit:
            found.add(
                "S010", "W", f"{keyword} SATNUM {satnum}", f"{len(table)} rows exceed TABDIMS NSSFUN {limit}"
            )


def check_satfunc(df: pd.DataFrame, found: Findings, tol: float) -> None:
    if df is None or df.empty:
        found.add("T001", "I", "satfunc", "no saturation function table found")
        return
    tables: dict[tuple[str, int], pd.DataFrame] = {}
    for (keyword, satnum), group in df.groupby(["KEYWORD", "SATNUM"], sort=False):
        spec = SAT_SPEC.get(keyword)
        if spec is None:
            found.add("T001", "I", f"{keyword} SATNUM {satnum}", f"{keyword} is not checked by this tool")
            continue
        sat_col, trends, pc_col = spec
        columns = [sat_col, *trends, *([pc_col] if pc_col else [])]
        columns = [c for c in columns if c in group.columns]
        table = group[columns].dropna(subset=[sat_col]).reset_index(drop=True)
        tables[(keyword, int(satnum))] = table
        where = f"{keyword} SATNUM {satnum}"
        sat = table[sat_col].to_numpy(dtype=float)
        if len(sat) < 2:
            found.add("S001", "E", where, "the table has fewer than 2 rows")
            continue
        if np.any(np.diff(sat) <= 0):
            found.add("S001", "E", where, f"{sat_col} is not strictly increasing")
        if sat.min() < -tol or sat.max() > 1 + tol:
            found.add("S002", "E", where, f"{sat_col} is outside 0 to 1 ({sat.min():g} to {sat.max():g})")
        for column, trend in trends.items():
            if column not in table:
                continue
            values = table[column].to_numpy(dtype=float)
            if np.isnan(values).any():
                found.add("S002", "E", where, f"{column} has undefined values")
                continue
            if values.min() < -tol or values.max() > 1 + tol:
                found.add(
                    "S002", "E", where, f"{column} is outside 0 to 1 ({values.min():g} to {values.max():g})"
                )
            bad = decreasing(values, tol) if trend == "inc" else increasing(values, tol)
            if bad:
                word = "decreases" if trend == "inc" else "increases"
                found.add("S003", "E", where, f"{column} {word} with {sat_col} at {bad} step(s)")
        if pc_col and pc_col in table:
            pc = table[pc_col].to_numpy(dtype=float)
            if not np.isnan(pc).any() and np.ptp(pc) > 0 and decreasing(pc, tol) and increasing(pc, tol):
                found.add("S004", "W", where, f"{pc_col} is not monotone")
        if keyword == "SWOF":
            if "KRW" in table and table["KRW"].iloc[0] > tol:
                found.add(
                    "S005", "W", where, f"KRW is {table['KRW'].iloc[0]:g} at Sw = {sat[0]:g}. Usually it is 0"
                )
            if abs(sat[-1] - 1.0) > tol:
                found.add(
                    "S006",
                    "I",
                    where,
                    f"the table ends at Sw = {sat[-1]:g}, not 1. This is normal when Sw max is 1 - Sor",
                )
        if keyword == "SGOF" and abs(sat[0]) > tol:
            found.add("S007", "W", where, f"the table starts at Sg = {sat[0]:g}, not 0")
    for (keyword, satnum), table in tables.items():
        if keyword != "SWOF" or ("SGOF", satnum) not in tables:
            continue
        swof, sgof = table, tables[("SGOF", satnum)]
        where = f"SATNUM {satnum}"
        if "KROW" in swof and "KROG" in sgof:
            a, b = float(swof["KROW"].iloc[0]), float(sgof["KROG"].iloc[0])
            if abs(a - b) > 1e-6:
                found.add("S008", "W", where, f"KROW at connate water is {a:g} but KROG at Sg = 0 is {b:g}")
        swl, sg_max = float(swof["SW"].iloc[0]), float(sgof["SG"].iloc[-1])
        if swl + sg_max > 1 + 1e-6:
            found.add("S009", "W", where, f"Swl {swl:g} + Sgmax {sg_max:g} is larger than 1")


def runs_of(frame: pd.DataFrame, key: str) -> list[pd.DataFrame]:
    """Split a table into consecutive groups with the same value in `key`."""
    marker = (frame[key] != frame[key].shift()).cumsum()
    return [group for _, group in frame.groupby(marker, sort=False)]


def check_pvt(df: pd.DataFrame, found: Findings, tol: float) -> None:
    if df is None or df.empty:
        found.add("T001", "I", "pvt", "no PVT table found")
        return
    for (keyword, pvtnum), table in df.groupby(["KEYWORD", "PVTNUM"], sort=False):
        where = f"{keyword} PVTNUM {pvtnum}"
        if keyword in ("PVTO", "PVDO", "PVTG", "PVDG"):
            for column in ("PRESSURE", "VOLUMEFACTOR", "VISCOSITY"):
                values = table[column].to_numpy(dtype=float)
                if np.isnan(values).any() or (values <= 0).any():
                    found.add("P001", "E", where, f"{column} is not positive everywhere")
            if "RS" in table and table["RS"].notna().any() and (table["RS"] < 0).any():
                found.add("P001", "E", where, "RS is negative")
        if keyword == "PVTO":
            groups = runs_of(table.reset_index(drop=True), "RS")
            rs = np.array([g["RS"].iloc[0] for g in groups], dtype=float)
            if len(rs) > 1 and np.any(np.diff(rs) <= 0):
                found.add("P002", "E", where, "RS is not strictly increasing between records")
            psat = np.array([g["PRESSURE"].iloc[0] for g in groups], dtype=float)
            bo_sat = np.array([g["VOLUMEFACTOR"].iloc[0] for g in groups], dtype=float)
            if len(psat) > 1 and decreasing(psat, tol):
                found.add("P003", "W", where, "saturated pressure decreases when RS increases")
            if len(bo_sat) > 1 and decreasing(bo_sat, tol):
                found.add("P003", "W", where, "saturated oil volume factor decreases when RS increases")
            bad_p = bad_bo = bad_mu = 0
            for g in groups:
                p = g["PRESSURE"].to_numpy(dtype=float)
                bad_p += int(np.sum(np.diff(p) <= 0))
                bad_bo += increasing(g["VOLUMEFACTOR"].to_numpy(dtype=float), tol)
                bad_mu += decreasing(g["VISCOSITY"].to_numpy(dtype=float), tol)
            if bad_p:
                found.add("P002", "E", where, f"pressure is not increasing inside {bad_p} record step(s)")
            if bad_bo:
                found.add(
                    "P003",
                    "W",
                    where,
                    f"undersaturated oil volume factor increases with pressure at {bad_bo} step(s)",
                )
            if bad_mu:
                found.add(
                    "P003",
                    "W",
                    where,
                    f"undersaturated oil viscosity decreases with pressure at {bad_mu} step(s)",
                )
        elif keyword in ("PVDO", "PVDG"):
            p = table["PRESSURE"].to_numpy(dtype=float)
            if np.any(np.diff(p) <= 0):
                found.add("P002", "E", where, "pressure is not strictly increasing")
            bo = table["VOLUMEFACTOR"].to_numpy(dtype=float)
            if increasing(bo, tol):
                code = "P003" if keyword == "PVDO" else "P004"
                found.add(code, "W", where, "volume factor increases with pressure")
        elif keyword == "PVTG":
            groups = runs_of(table.reset_index(drop=True), "PRESSURE")
            p = np.array([g["PRESSURE"].iloc[0] for g in groups], dtype=float)
            if len(p) > 1 and np.any(np.diff(p) <= 0):
                found.add("P002", "E", where, "pressure is not strictly increasing between records")
        elif keyword == "PVTW":
            for column in ("PRESSURE", "VOLUMEFACTOR", "VISCOSITY", "COMPRESSIBILITY"):
                values = table[column].to_numpy(dtype=float)
                if np.isnan(values).any() or (values <= 0).any():
                    found.add("P001", "E", where, f"{column} is not positive")
        elif keyword == "ROCK":
            values = table["COMPRESSIBILITY"].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values < 0).any():
                found.add("P001", "E", where, "COMPRESSIBILITY is negative or nonfinite")
            if (values == 0).any():
                found.add("P005", "W", where, "COMPRESSIBILITY is zero")
        elif keyword == "DENSITY":
            cols = ["OILDENSITY", "WATERDENSITY", "GASDENSITY"]
            if (table[cols].to_numpy(dtype=float) <= 0).any():
                found.add("P001", "E", where, "a density is not positive")


def first_lines(error: Exception, count: int = 2) -> str:
    return " ".join(str(error).splitlines()[:count])


def check_equil(df: pd.DataFrame, found: Findings) -> None:
    if df is None or df.empty or "KEYWORD" not in df:
        found.add("T001", "I", "equil", "no EQUIL table found")
        return
    equil_rows = df[df["KEYWORD"] == "EQUIL"].reset_index(drop=True)
    if equil_rows.empty:
        found.add("T001", "I", "equil", "no EQUIL record found")
    for index, row in equil_rows.iterrows():
        where = f"EQUIL record {index + 1}"
        if not row["PRESSURE"] > 0:
            found.add("E001", "E", where, f"datum pressure is {row['PRESSURE']}")
        if pd.notna(row.get("OWC")) and pd.notna(row.get("GOC")) and row["GOC"] > row["OWC"]:
            found.add("E002", "E", where, f"GOC {row['GOC']:g} is deeper than OWC {row['OWC']:g}")


def check_vfp(tables: list[pd.DataFrame], found: Findings, deck_units: str | None) -> None:
    for table in tables:
        keyword = str(table["VFP_TYPE"].iloc[0])
        number = int(table["TABLE_NUMBER"].iloc[0])
        where = f"{keyword} {number}"
        bhp = table["TAB"].to_numpy(dtype=float)
        if not np.isfinite(bhp).all() or (bhp <= 0).any():
            found.add("V001", "E", where, "TAB has values that are not finite or not positive")
        datum = table["DATUM"].iloc[0] if "DATUM" in table else None
        if datum is None or pd.isna(datum) or float(datum) == 0.0:
            found.add("V003", "W", where, f"datum depth is {datum}")
        unit = str(table["UNIT_TYPE"].iloc[0]) if "UNIT_TYPE" in table else ""
        if deck_units and unit and unit.upper() != deck_units:
            found.add("V004", "W", where, f"table unit system is {unit}, deck is {deck_units}")
        others = [c for c in ("RATE", "WFR", "GFR", "ALQ") if c in table]
        if keyword == "VFPPROD" and "PRESSURE" in table:
            bad = 0
            for _, group in table.groupby(others, sort=False):
                ordered = group.sort_values("PRESSURE")
                bad += decreasing(ordered["TAB"].to_numpy(dtype=float), 1e-9)
            if bad:
                found.add("V002", "W", where, f"BHP falls when THP rises at {bad} step(s)")


def scan_includes(deck: Path, max_depth: int = 6) -> tuple[list[Path], list[tuple[Path, int, str]]]:
    """Follow INCLUDE and IMPORT lines with plain text rules. No parser is needed.

    Returns the files that exist and the references that do not: (file, line, path).
    A path is relative to the file that holds the keyword. A path with a variable ($ or <>) is skipped.
    """
    found: list[Path] = []
    missing: list[tuple[Path, int, str]] = []
    seen: set[Path] = set()

    def walk(path: Path, depth: int) -> None:
        if path in seen or depth > max_depth:
            return
        seen.add(path)
        lines = path.read_text(encoding="latin-1").splitlines()
        waiting = None  # the keyword (INCLUDE or IMPORT) whose path is still to come
        for number, raw in enumerate(lines, start=1):
            line = raw.split("--", 1)[0].strip()
            if not line:
                continue
            word = line.split(None, 1)[0].upper()
            if word in ("INCLUDE", "IMPORT"):
                waiting = word
                line = line[len(word) :].strip()
                if not line:
                    continue
            if waiting:
                keyword, waiting = waiting, None
                match = re.match(r"""['"]?([^'"\s/][^'"\s]*|/[^'"\s]*)['"]?""", line)
                if not match:
                    continue
                target = match.group(1).rstrip("/") if match.group(1).endswith(" /") else match.group(1)
                if "$" in target or "<" in target:
                    continue
                full = Path(os.path.normpath(path.parent / target))
                if full.is_file():
                    found.append(full)
                    if (
                        keyword == "INCLUDE"
                    ):  # an IMPORT target is binary or a grid file. Do not read it as text
                        walk(full, depth + 1)
                else:
                    missing.append((path, number, target))

    walk(deck.resolve(), 0)
    return found, missing


FAMILIES = {
    "satfunc": ("SWOF", "SGOF", "SWFN", "SGFN", "SOF3", "SOF2"),
    "pvt": ("PVTO", "PVDO", "PVTG", "PVDG", "PVTW", "ROCK", "DENSITY"),
    "equil": ("EQUIL",),
    "VFPPROD": ("VFPPROD",),
    "VFPINJ": ("VFPINJ",),
}


def keywords_in(text: str) -> set[str]:
    """Keywords that start a line, outside comments. res2df is slow when it must guess table sizes."""
    found: set[str] = set()
    for line in text.splitlines():
        word = line.split("--", 1)[0].strip().split(" ", 1)[0].upper()
        if word:
            found.add(word)
    return found


# Keywords that give the table sizes and the phases. A table include often needs a file that has them.
CONTEXT_KEYWORDS = {
    "TABDIMS", "EQLDIMS", "REGDIMS", "ENDSCALE", "SATOPTS", "METRIC", "FIELD", "LAB", "OIL", "GAS", "WATER",
    "DISGAS", "VAPOIL",
}  # fmt: skip
PHASES = {"OIL", "GAS", "WATER"}


def family_text(texts: dict[Path, str], keywords: dict[Path, set[str]], family: str) -> str | None:
    """Text for one table family: the files with its keywords, after the files that give sizes and phases.

    Each family is read alone. A parse error in one family then cannot spoil another family.
    """
    own = {path for path, words in keywords.items() if words & set(FAMILIES[family])}
    if not own:
        return None
    context = [p for p in texts if p not in own and keywords[p] & CONTEXT_KEYWORDS]
    ordered = [*context, *[p for p in texts if p in own]]
    text = "\n".join(texts[p] for p in ordered)
    if family == "equil" and not set().union(*(keywords[p] for p in ordered)) & PHASES:
        text = "OIL\nGAS\nWATER\n" + text  # equil.df needs the phases
    return text


# TABDIMS items 1 to 6 and their defaults
TABDIMS_DEFAULTS = {"NTSFUN": 1, "NTPVT": 1, "NSSFUN": 20, "NPPVT": 20, "NTFIP": 1, "NRPVT": 20}
SAT_COLUMNS = {"SWOF": 4, "SGOF": 4, "SLGOF": 4, "SWFN": 3, "SGFN": 3, "SOF3": 3, "SOF2": 2}
PVT_COLUMNS = {"PVDO": 3, "PVDG": 3, "PVTO": None, "PVTG": None}


def keyword_records(text: str, keywords: set[str]) -> list[tuple[str, list[list[str]]]]:
    """Slash records of each occurrence of the keywords, in text order. 'n*' and 'n*v' are expanded.

    A table reader that trusts TABDIMS drops what does not fit. This text scan counts what the deck has.
    """
    found: list[tuple[str, list[list[str]]]] = []
    records: list[list[str]] | None = None
    record: list[str] = []
    for raw in text.splitlines():
        line = raw.split("--", 1)[0].strip()
        if not line:
            continue
        word = line.split(None, 1)[0].upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_-]{0,7}", word):
            records = [] if word in keywords else None
            record = []
            if records is not None:
                found.append((word, records))
            continue
        if records is None:
            continue
        for token in re.findall(r"/|[^\s,/]+", line):
            if token == "/":
                records.append(record)
                record = []
                continue
            repeat = re.fullmatch(r"(\d+)\*(.*)", token)
            record.extend([repeat[2] or "*"] * int(repeat[1]) if repeat else [token])
    return found


def tabdims(text: str) -> dict[str, int] | None:
    """TABDIMS items with defaults, or None when the text has no TABDIMS."""
    occurrences = keyword_records(text, {"TABDIMS"})
    if not occurrences or not occurrences[0][1]:
        return None
    values = occurrences[0][1][0]
    out = {}
    for index, (name, default) in enumerate(TABDIMS_DEFAULTS.items()):
        value = values[index] if index < len(values) else "*"
        out[name] = default if value == "*" else int(value)
    return out


def table_sizes(records: list[list[str]], columns: int | None) -> list[tuple[int, int]]:
    """(outer nodes, rows) for each table. A plain table: (1, rows). PVTO/PVTG: (records, largest inner rows).

    A PVTO or PVTG record is one outer value and triples. An empty record ends the table.
    """
    if columns is not None:
        return [(1, -(-len(record) // columns)) for record in records]
    tables: list[tuple[int, int]] = []
    current: list[list[str]] = []
    for record in records:
        if record:
            current.append(record)
            continue
        if current:
            tables.append((len(current), max((len(r) - 1) // 3 for r in current)))
        current = []
    if current:
        tables.append((len(current), max((len(r) - 1) // 3 for r in current)))
    return tables


def check_tabdims(text: str, found: Findings, use_defaults: bool) -> None:
    """T002: more tables than NTSFUN or NTPVT. T003: PVT rows or records above NPPVT or NRPVT.

    Saturation rows above NSSFUN are S010 (deck mode), so T003 does not repeat them.

    Flow accepts a table with more rows than NSSFUN (measured on the Egg model: 91 rows, NSSFUN 24).
    Eclipse stops with an error (from the manual, not tested). res2df reads only NTSFUN tables (measured).
    """
    dims = tabdims(text)
    source = "TABDIMS"
    if dims is None:
        if not use_defaults:
            return
        dims, source = TABDIMS_DEFAULTS, "TABDIMS (absent, defaults)"
    for keyword, records in keyword_records(text, set(SAT_COLUMNS) | set(PVT_COLUMNS)):
        saturation = keyword in SAT_COLUMNS
        sizes = table_sizes(records, SAT_COLUMNS[keyword] if saturation else PVT_COLUMNS[keyword])
        count_item = "NTSFUN" if saturation else "NTPVT"
        if len(sizes) > dims[count_item]:
            found.add(
                "T002",
                "E",
                keyword,
                f"{len(sizes)} tables but {source} {count_item} is {dims[count_item]}."
                " res2df reads only the first tables. The simulator needs the right count",
            )
        if saturation:
            limits = (None, None)
        elif keyword == "PVTO":
            limits = ("NRPVT", "NPPVT")
        elif keyword == "PVTG":
            limits = ("NPPVT", "NRPVT")
        else:
            limits = (None, "NPPVT")
        for number, (outer, rows) in enumerate(sizes, start=1):
            for item, size, what in ((limits[0], outer, "records"), (limits[1], rows, "rows")):
                if item and size > dims[item]:
                    found.add(
                        "T003",
                        "W",
                        f"{keyword} table {number}",
                        f"{size} {what}, more than {source} {item} {dims[item]}."
                        " Flow accepts it (measured). Eclipse stops (from the manual, not tested)",
                    )


def run_deck(args, found: Findings, counts: dict) -> None:
    """Whole-deck mode. The OPM parser reads the deck and all its INCLUDE files."""
    existing, missing = scan_includes(args.deck)
    counts["_includes"] = len(existing)
    for path, line, target in missing:
        found.add("I001", "E", f"{path.name}:{line}", f"include file not found: {target}")
    texts = [args.deck.read_text(encoding="latin-1")]
    texts += [path.read_text(encoding="latin-1") for path in existing if path.suffix.upper() != ".EGRID"]
    check_tabdims("\n".join(texts), found, use_defaults=True)
    source = ResdataFiles(str(args.deck))
    try:
        deck = source.get_deck()
    except Exception as error:
        found.add("L001", "E", str(args.deck), f"cannot read the deck: {first_lines(error)}")
        return
    found.rows.extend(check_opm_deck(deck))
    units = [k for k in UNIT_KEYWORDS if k in deck]
    units_name = units[0] if units else None
    counts["_units"] = "METRIC" if units_name == "PVT-M" else units_name
    checks = {
        "satfunc": (satfunc.df, check_satfunc),
        "pvt": (pvt.df, check_pvt),
        "equil": (equil.df, check_equil),
    }
    for name, (load, check) in checks.items():
        try:
            df = load(deck)
            if name == "satfunc" and df is not None and not df.empty and "TABDIMS" in deck:
                check_satfunc_rows(df, deck["TABDIMS"][0][2].get_int(0), found)
        except Exception as error:
            found.add("L001", "E", name, f"cannot read {name} tables: {first_lines(error)}")
            continue
        counts[name] = 0 if df is None else len(df)
        check(df, found, args.tol) if name != "equil" else check(df, found)
    for keyword in ("VFPPROD", "VFPINJ"):
        try:
            tables = vfp_module.dfs(deck, keyword=keyword)
        except Exception as error:
            found.add("L001", "E", keyword, f"cannot read {keyword} tables: {first_lines(error)}")
            continue
        counts[keyword] = len(tables)
        check_vfp(tables, found, counts["_units"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--deck", type=Path, default=None, help="A .DATA file")
    parser.add_argument(
        "--include", type=Path, action="append", default=[], help="A table include file. Repeat."
    )
    parser.add_argument("--tol", type=float, default=1e-9, help="Tolerance for monotone and range checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.deck) == bool(args.include):
        print("ERROR: give --deck or --include, not both and not neither", file=sys.stderr)
        return 2
    for path in [args.deck, *args.include]:
        if path is not None and not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2

    found = Findings()
    deck_units = None
    includes_found = None
    counts: dict[str, int] = {}
    if args.deck:
        run_deck(args, found, counts)
        deck_units = counts.pop("_units", None)
        includes_found = counts.pop("_includes", 0)
    else:
        texts = {path: path.read_text(encoding="latin-1") for path in args.include}
        keywords = {path: keywords_in(text) for path, text in texts.items()}
        all_words = set().union(*keywords.values())
        units = [k for k in ("METRIC", "FIELD", "LAB") if k in all_words]
        deck_units = units[0] if units else None
        check_tabdims("\n".join(texts.values()), found, use_defaults=False)
        checks = {"satfunc": (satfunc, check_satfunc), "pvt": (pvt, check_pvt), "equil": (equil, check_equil)}
        for family, (module, check) in checks.items():
            text = family_text(texts, keywords, family)
            if text is None:
                found.add("T001", "I", family, f"no {family} keyword in the include files")
                continue
            try:
                df = module.df(text)
            except Exception as error:
                found.add(
                    "L001",
                    "E",
                    family,
                    f"cannot read {family} tables: {first_lines(error)}. "
                    "If the message is about table sizes, add the include file with TABDIMS or EQLDIMS",
                )
                continue
            counts[family] = 0 if df is None else len(df)
            check(df, found, args.tol) if family != "equil" else check(df, found)
        for keyword in ("VFPPROD", "VFPINJ"):
            text = family_text(texts, keywords, keyword)
            if text is None:
                continue
            try:
                tables = vfp_module.dfs(text, keyword=keyword)
            except Exception as error:
                found.add("L001", "E", keyword, f"cannot read {keyword} tables: {first_lines(error)}")
                continue
            counts[keyword] = len(tables)
            check_vfp(tables, found, deck_units)
    if counts.get("VFPPROD", 0) + counts.get("VFPINJ", 0) == 0:
        found.add("T001", "I", "vfp", "no VFP table found")

    errors = [r for r in found.rows if r["severity"] == "E"]
    warnings = [r for r in found.rows if r["severity"] == "W"]
    result = {
        "source": str(args.deck) if args.deck else [str(p) for p in args.include],
        "unit_system": deck_units,
        "rows_read": counts,
        "include_files_found": includes_found,
        "findings": found.rows,
        "errors": len(errors),
        "warnings": len(warnings),
    }
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Source: {result['source']}  units: {deck_units}  rows read: {counts}")
        for row in found.rows:
            print(f"{row['severity']} {row['code']} [{row['where']}] {row['message']}")
        print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
