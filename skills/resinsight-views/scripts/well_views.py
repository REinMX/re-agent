#!/usr/bin/env python3
"""Make a ViewSpec of well-completion sections from the output of grid-property-qc. Standard library only.

Input: the folder of completion_in_grid.py (completion_in_grid.json and connections.csv).
Output, in --out:
  COMPFLAG.grdecl  whole-grid array: 0 no connection, 1 ok, 2 warning flag, 3 error flag
  spec.json        for each well: a section through the well row (camera north) of a property and of COMPFLAG
Then render it with render_views.py --spec <out>/spec.json.

COMP-INACTIVE and COMP-OUTSIDE cells are not drawn by ResInsight (inactive or outside). They are in
spec.json "notes" and in wells.json, so the report can name them.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grdecl  # noqa: E402
from view_spec import PropertyRef, SpecError, parse_spec  # noqa: E402

FLAG_CODE = {"ok": 1, "COMP-LOW-PERM": 2}  # every other flag is an error: 3
NOT_DRAWN = ("COMP-INACTIVE", "COMP-OUTSIDE")


@dataclass
class WellCells:
    well: str
    rows: Counter = field(default_factory=Counter)  # j of each connection
    cells: dict = field(default_factory=dict)  # (i, j, k) -> worst flag code
    hidden: list = field(default_factory=list)  # (i, j, k, flag) not drawn, as text from the CSV

    def row(self) -> int:
        """The j that has the most connections (a deviated well crosses more than one row)."""
        return self.rows.most_common(1)[0][0]


def read_completions(folder: Path) -> tuple[tuple[int, int, int], dict[str, WellCells]]:
    summary_path, table = folder / "completion_in_grid.json", folder / "connections.csv"
    for path in (summary_path, table):
        if not path.is_file():
            raise SpecError(f"missing {path}. Run grid-property-qc completion_in_grid.py first")
    dims = tuple(int(v) for v in json.loads(summary_path.read_text())["dims"])
    if len(dims) != 3 or min(dims) < 1:
        raise SpecError(f"{summary_path}: dims {dims} is not three positive integers")
    wells: dict[str, WellCells] = {}
    with table.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"well", "i", "j", "k", "flag"} - set(reader.fieldnames or ())
        if missing:
            raise SpecError(f"{table}: missing columns {sorted(missing)}")
        for line, record in enumerate(reader, start=2):
            flag = record["flag"]
            entry = wells.setdefault(record["well"], WellCells(record["well"]))
            if flag in NOT_DRAWN:  # COMP-OUTSIDE can have an empty i, j, k (not resolved)
                hidden = (record["i"] or "?", record["j"] or "?", record["k"] or "?", flag)
                if hidden not in entry.hidden:
                    entry.hidden.append(hidden)
                continue
            try:
                i, j, k = int(record["i"]), int(record["j"]), int(record["k"])
            except ValueError:
                raise SpecError(f"{table}:{line}: i, j, k are not integers") from None
            entry.rows[j] += 1
            code = FLAG_CODE.get(flag, 3)
            entry.cells[(i, j, k)] = max(code, entry.cells.get((i, j, k), 0))
    if not wells:
        raise SpecError(f"{table} has no connections")
    return dims, wells


def flag_array(dims: tuple[int, int, int], wells: dict[str, WellCells]) -> list[float]:
    nx, ny, nz = dims
    values = [0.0] * (nx * ny * nz)
    for entry in wells.values():
        for (i, j, k), code in entry.cells.items():
            if 1 <= i <= nx and 1 <= j <= ny and 1 <= k <= nz:
                g = (i - 1) + nx * ((j - 1) + ny * (k - 1))
                values[g] = max(values[g], float(code))
    return values


def choose(wells: dict[str, WellCells], names: list[str] | None, flagged: bool) -> list[WellCells]:
    if names:
        unknown = [n for n in names if n not in wells]
        if unknown:
            raise SpecError(f"wells not in connections.csv: {', '.join(unknown)}")
        return [wells[n] for n in names]
    return [w for w in wells.values() if not flagged or w.hidden or any(c > 1 for c in w.cells.values())]


def view_name(well: str, suffix: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{well}_{suffix}").lstrip("_.-")


def build(args: argparse.Namespace) -> dict:
    dims, wells = read_completions(args.completions)
    PropertyRef.parse(args.property)
    picked = choose(wells, args.wells.split(",") if args.wells else None, args.only_flagged)
    drawn = [w for w in picked if w.cells]  # a well with no drawn cell has no row to show; it stays in notes
    if not drawn:
        raise SpecError("no well to show (with --only-flagged: no well has a flag)")
    if len(drawn) > args.max_wells:
        raise SpecError(f"{len(drawn)} wells; the limit is {args.max_wells}. Give --wells or --max-wells")
    args.out.mkdir(parents=True, exist_ok=True)
    array = args.out / "COMPFLAG.grdecl"
    grdecl.write_grdecl(array, "COMPFLAG", flag_array(dims, wells))
    views = []
    for entry in drawn:
        j = entry.row()
        common = {
            "time_step": args.time_step,
            "range": {"j": [j, j]},
            "camera": "north",
            "z_scale": args.z_scale,
            "zoom": args.zoom,
        }
        views.append({"name": view_name(entry.well, "section"), "property": args.property, **common})
        views.append(
            {"name": view_name(entry.well, "compflag"), "property": "INPUT_PROPERTY:COMPFLAG", **common}
        )
    notes = [
        f"{entry.well}: {flag} at ({i},{j},{k}) is not drawn"
        for entry in picked
        for i, j, k, flag in entry.hidden
    ]
    spec = {
        "case": str(args.case),
        "out": str(args.out / "render"),
        "title": "Well completions",
        "arrays": [{"name": "COMPFLAG", "file": str(array)}],
        "views": views,
        "notes": notes + ["COMPFLAG: 0 no connection, 1 ok, 2 warning flag, 3 error flag"],
    }
    parse_spec(spec)  # the same checks as render_views.py
    (args.out / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    wells_out = {
        e.well: {"row_j": e.row() if e.cells else None, "cells": len(e.cells), "hidden": e.hidden}
        for e in picked
    }
    (args.out / "wells.json").write_text(json.dumps(wells_out, indent=2) + "\n")
    return {
        "spec": str(args.out / "spec.json"),
        "array": str(array),
        "wells": list(wells_out),
        "views": len(views),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--completions", required=True, type=Path, help="Output folder of completion_in_grid.py"
    )
    parser.add_argument("--case", required=True, type=Path, help="BASE.EGRID to show")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--property", default="STATIC_NATIVE:PERMX")
    parser.add_argument("--time-step", default="first")
    parser.add_argument("--wells", help="Comma-separated well names. Default: all wells")
    parser.add_argument("--only-flagged", action="store_true", help="Only wells with a warning or error flag")
    parser.add_argument("--max-wells", type=int, default=12)
    parser.add_argument("--z-scale", type=float, default=10.0)
    parser.add_argument("--zoom", type=float, default=2.0, help="1 to 20. Moves the side camera closer")
    args = parser.parse_args(argv)
    args.case, args.out = args.case.resolve(), args.out.resolve()
    if args.time_step.isdigit():
        args.time_step = int(args.time_step)
    try:
        result = build(args)
    except (SpecError, grdecl.GrdeclError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
