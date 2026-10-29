#!/usr/bin/env python3
"""Check each COMPDAT connection of a deck against the grid and the INIT arrays of its case. Read-only.

Usage:
    completion_in_grid.py --data <DECK.DATA> --case <BASE or BASE.EGRID> --outdir <new dir> [--min-perm 0.01] [--force]

res2df reads COMPDAT and fills the defaulted I and J from WELSPECS. If res2df cannot read the deck, the exit is 2.
Output in --outdir: completion_in_grid.json, connections.csv, report.md.
Exit code: 0 = no error finding, 1 = at least one error finding, 2 = missing input.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

from grid_lib import (
    Finding,
    GridCase,
    InputError,
    exit_code,
    fail,
    finding_dicts,
    findings_markdown,
    load_case,
    need_packages,
    prepare_outdir,
    write_json,
)

SCHEMA_VERSION = 1
SAMPLE = 20
COLUMNS = ["well", "date", "i", "j", "k", "status", "active", "porv", "permx", "permy", "poro", "flag"]


def read_compdat(deck: Path):
    """COMPDAT rows from res2df, one row per layer. Raise InputError when res2df fails or finds none."""
    import res2df

    try:
        frame = res2df.compdat.df(res2df.ResdataFiles(str(deck)))
    except Exception as exc:  # noqa: BLE001 - any parser failure is a missing input
        raise InputError(f"res2df cannot read {deck}: {type(exc).__name__}: {exc}") from exc
    if frame.empty:
        raise InputError(f"{deck} has no COMPDAT connections")
    return frame


def as_int(value) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) else None


def text(value) -> str:
    """A table cell as text. None, NaN, and NaT are empty."""
    return "" if value is None or value != value else str(value)


def classify(case: GridCase, i, j, k, min_perm: float) -> dict:
    """The grid values and the flag of one connection cell."""
    import numpy as np

    nx, ny, nz = case.dims
    row = {"active": False, "porv": "", "permx": "", "permy": "", "poro": ""}
    if None in (i, j, k) or not (1 <= i <= nx and 1 <= j <= ny and 1 <= k <= nz):
        return row | {"flag": "COMP-OUTSIDE"}
    g = case.global_of(i, j, k)
    porv = float(case.porv[g])
    row["porv"] = porv
    position = np.searchsorted(case.active_global, g)
    if position >= case.n_active or case.active_global[position] != g:
        return row | {"flag": "COMP-INACTIVE"}
    values = {
        n.lower(): float(case.arrays[n][position]) for n in ("PERMX", "PERMY", "PORO") if n in case.arrays
    }
    row |= values | {"active": True}
    if porv <= 0:
        return row | {"flag": "COMP-ZERO-PORV"}
    permx, permy = values.get("permx"), values.get("permy")
    if permx is not None and permy is not None:
        if permx == 0 and permy == 0:
            return row | {"flag": "COMP-ZERO-PERM"}
        if math.sqrt(max(permx, 0) * max(permy, 0)) < min_perm:
            return row | {"flag": "COMP-LOW-PERM"}
    return row | {"flag": "ok"}


MESSAGES = {
    "COMP-OUTSIDE": ("error", "connection cell outside the grid dimensions, or I, J, K not resolved"),
    "COMP-INACTIVE": ("error", "connection in an inactive cell"),
    "COMP-ZERO-PORV": ("error", "connection in an active cell with PORV <= 0"),
    "COMP-ZERO-PERM": ("error", "connection in a cell with PERMX = 0 and PERMY = 0"),
    "COMP-LOW-PERM": ("warning", "connection in a cell with sqrt(PERMX * PERMY) below --min-perm"),
}


def findings_of(rows: list[dict]) -> list[Finding]:
    """One finding per flag, counted over unique (well, i, j, k). One finding per well with no active cell."""
    findings = []
    for code, (severity, text) in MESSAGES.items():
        cells = sorted({(r["well"], r["i"], r["j"], r["k"]) for r in rows if r["flag"] == code}, key=str)
        if cells:
            wells = sorted({c[0] for c in cells})
            message = f"{text}; wells {', '.join(wells[:10])}{' ...' if len(wells) > 10 else ''}"
            sample = [(c[1], c[2], c[3]) for c in cells[:SAMPLE]]
            findings.append(Finding(code, severity, message, len(cells), sample))
    for well in sorted({r["well"] for r in rows}):
        mine = [r for r in rows if r["well"] == well]
        if not any(r["active"] for r in mine):
            cells = sorted({(r["i"], r["j"], r["k"]) for r in mine}, key=str)[:SAMPLE]
            findings.append(
                Finding(
                    "COMP-WELL-NO-OPEN-CELL",
                    "error",
                    f"well {well}: no connection is in an active cell",
                    1,
                    cells,
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", required=True, type=Path, help="The DATA file of the deck")
    parser.add_argument("--case", required=True, help="BASE or BASE.EGRID. BASE.INIT must be next to it")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--min-perm", type=float, default=0.01, help="mD. Warn below sqrt(PERMX * PERMY)")
    parser.add_argument("--force", action="store_true", help="Write into a folder that is not empty")
    args = parser.parse_args(argv)
    missing = need_packages("numpy", "resdata", "res2df")
    if missing:
        return fail(f"{missing} is not installed. Use the Komodo Python or install {missing}")
    if not args.data.is_file():
        return fail(f"not a file: {args.data}")
    try:
        case = load_case(args.case)
        prepare_outdir(args.outdir, case.base, args.force)
        if args.data.resolve().parent in [args.outdir.resolve(), *args.outdir.resolve().parents]:
            raise InputError(f"--outdir {args.outdir} is in the deck folder. Choose a folder outside it")
        frame = read_compdat(args.data)
    except InputError as exc:
        return fail(str(exc))

    rows = []
    for record in frame.to_dict("records"):
        i, j = as_int(record.get("I")), as_int(record.get("J"))
        k1, k2 = as_int(record.get("K1")), as_int(record.get("K2"))
        layers = range(k1, k2 + 1) if None not in (k1, k2) else [None]
        for k in layers:
            base = {
                "well": record["WELL"],
                "date": text(record.get("DATE")),
                "i": i,
                "j": j,
                "k": k,
                "status": text(record.get("OP/SH")),
            }
            rows.append(base | classify(case, i, j, k, args.min_perm))
    findings = findings_of(rows)
    notes = [
        f"{n} is not in the INIT file; COMP-ZERO-PERM and COMP-LOW-PERM did not run"
        for n in ("PERMX", "PERMY")
        if n not in case.arrays
    ]

    with (args.outdir / "connections.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({c: r.get(c, "") for c in COLUMNS} for r in rows)
    wells = sorted({r["well"] for r in rows})
    write_json(
        args.outdir / "completion_in_grid.json",
        {
            "schema_version": SCHEMA_VERSION,
            "data": str(args.data),
            "case": str(case.base),
            "dims": list(case.dims),
            "n_active": case.n_active,
            "wells": len(wells),
            "connections": len(rows),
            "min_perm": args.min_perm,
            "findings": finding_dicts(findings),
            "notes": notes,
        },
    )
    lines = [
        f"# Completions in grid: {args.data.name}",
        "",
        f"{len(wells)} wells, {len(rows)} connection rows (all dates) on grid {' x '.join(map(str, case.dims))}.",
        "Cells are (i, j, k), 1-based. Counts are unique (well, i, j, k).",
        "",
        *findings_markdown(findings),
    ]
    if notes:
        lines += ["", "## Notes", *[f"- {n}" for n in notes]]
    (args.outdir / "report.md").write_text("\n".join(lines) + "\n")
    errors = sum(f.severity == "error" for f in findings)
    print(
        f"completion_in_grid: {len(wells)} wells, {len(rows)} connections, {len(findings)} findings, {errors} errors. Wrote {args.outdir}"
    )
    for f in findings:
        print(f"FINDING {f.code} {f.severity} count={f.count}: {f.message}")
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
