#!/usr/bin/env python3
"""Remaining-oil map and infill candidates of one case at one report step, with resdata.

Mobile oil per cell = PORV * max(SOIL - SOR, 0), SOIL = 1 - SWAT - SGAS (SGAS = 0 when the case has none).
Per column (i, j) the mobile oil is summed over the active layers. The drainage score of a column is the
mobile oil of all columns within --radius cells (a square window), so the centre of a large unswept area
ranks above a single rich column. A candidate is the column with the largest score that is at least --min-distance cells (Chebyshev, in i and j) from every well
connection and from every candidate picked before it. The ranking is greedy.
Outputs in --outdir: remaining_oil.json, columns.csv, candidates.csv, report.md,
arrays/MOBOIL.grdecl (per cell), arrays/MOBCOL.grdecl (the column sum in every cell of the column),
arrays/CANDID.grdecl (rank of the candidate column, 0 elsewhere) and spec.json for render_views.py.
Exit 0 at least one candidate, 1 no candidate, 2 input missing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "resinsight-views" / "scripts"))

import grdecl  # noqa: E402

SCHEMA_VERSION = 1


class InputError(Exception):
    """Missing input. Exit 2."""


@dataclass(frozen=True)
class Candidate:
    rank: int
    i: int
    j: int
    mobile_oil_rm3: float  # in the window of --radius cells
    share_of_total: float
    nearest_well: str
    distance_cells: int


def resolve_step(dates: list[str], step: str) -> int:
    if step == "last":
        return len(dates) - 1
    if step.isdigit():
        index = int(step)
        if index >= len(dates):
            raise InputError(f"--step {index}: the case has {len(dates)} report steps")
        return index
    if step in dates:
        return dates.index(step)
    raise InputError(f"--step {step} is not a report step. Steps: {dates[0]} .. {dates[-1]} ({len(dates)})")


def read_case(base: Path, step: str):
    import numpy as np
    from resdata.grid import Grid
    from resdata.resfile import ResdataFile

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    for suffix in (".EGRID", ".INIT", ".UNRST"):
        if not base.with_name(base.name + suffix).is_file():
            raise InputError(f"missing {base.name}{suffix} in {base.parent}")
    grid = Grid(f"{base}.EGRID")
    nx, ny, nz, n_active = grid.get_dims()
    active_global = np.flatnonzero(np.asarray(grid.export_actnum().numpy_copy()) & 1)
    porv = np.asarray(ResdataFile(f"{base}.INIT")["PORV"][0].numpy_copy(), dtype=float)[active_global]
    rst = ResdataFile(f"{base}.UNRST")
    dates = [d.strftime("%Y-%m-%d") for d in rst.dates]
    index = resolve_step(dates, step)
    view = rst.restart_view(seqnum_index=index)  # iget_named_kw counts occurrences, not steps
    if "SWAT" not in view:
        raise InputError(f"{base.name}.UNRST has no SWAT at step {index} ({dates[index]})")
    swat = np.asarray(view["SWAT"][0].numpy_copy(), dtype=float)
    sgas = np.asarray(view["SGAS"][0].numpy_copy(), dtype=float) if "SGAS" in view else 0.0 * swat
    if len(swat) != n_active:
        raise InputError(f"SWAT has {len(swat)} values, the grid has {n_active} active cells")
    return (nx, ny, nz), active_global, porv, 1.0 - swat - sgas, dates[index], index


def read_wells(path: Path | None) -> dict[str, list[tuple[int, int]]]:
    if path is None:
        return {}
    if path.is_dir():
        path = path / "connections.csv"
    if not path.is_file():
        raise InputError(f"missing {path}. Run grid-property-qc completion_in_grid.py first")
    wells: dict[str, list[tuple[int, int]]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            wells.setdefault(row["well"], []).append((int(row["i"]), int(row["j"])))
    return wells


def window_sum(column_oil, radius: int):
    """Sum of each (2*radius+1)^2 window, clipped at the grid edge."""
    import numpy as np

    padded = np.pad(column_oil, radius)
    table = padded.cumsum(0).cumsum(1)
    table = np.pad(table, ((1, 0), (1, 0)))
    w = 2 * radius + 1
    return table[w:, w:] - table[:-w, w:] - table[w:, :-w] + table[:-w, :-w]


def pick(
    column_oil, wells, extra_wells, min_distance: int, count: int, min_share: float, radius: int = 0
) -> list[Candidate]:
    import numpy as np

    total = float(column_oil.sum())
    score = window_sum(column_oil, radius) if radius else column_oil
    # equal scores: the richer column first
    order = np.lexsort((-column_oil.reshape(-1), -score.reshape(-1)))
    points = [(name, i, j) for name, cells in wells.items() for i, j in set(cells)] + extra_wells
    picked: list[Candidate] = []
    for flat in order:
        if len(picked) >= count:
            break
        i0, j0 = np.unravel_index(flat, column_oil.shape)
        i, j = int(i0) + 1, int(j0) + 1
        oil = float(score[i0, j0])
        if oil <= 0 or oil / total < min_share:  # sorted: no later column has more
            break
        near = [(max(abs(i - pi), abs(j - pj)), name) for name, pi, pj in points]
        near += [(max(abs(i - c.i), abs(j - c.j)), f"candidate {c.rank}") for c in picked]
        distance, name = min(near) if near else (10**9, "")
        if distance < min_distance:
            continue
        picked.append(Candidate(len(picked) + 1, i, j, oil, oil / total, name, int(distance)))
    return picked


def run(args: argparse.Namespace) -> int:
    import numpy as np

    base = args.case.with_suffix("") if args.case.suffix.upper() in (".EGRID", ".DATA") else args.case
    dims, active_global, porv, soil, date, index = read_case(base, args.step)
    nx, ny, nz = dims
    mobile = porv * np.clip(soil - args.sor, 0.0, None)
    full = np.zeros(nx * ny * nz)
    full[active_global] = mobile
    cube = full.reshape((nz, ny, nx))  # global index = i + nx*(j + ny*k)
    column_oil = cube.sum(axis=0).T  # (nx, ny)
    wells = read_wells(args.completions)
    extra = []
    for item in args.avoid or []:
        name, _, ij = item.partition("=")
        try:
            ai, aj = (int(v) for v in ij.split(","))
        except ValueError:
            raise InputError(f"--avoid {item}: use NAME=I,J") from None
        extra.append((name, ai, aj))
    candidates = pick(column_oil, wells, extra, args.min_distance, args.count, args.min_share, args.radius)
    out = args.outdir.resolve()
    if base.resolve().parent in [out, *out.parents]:
        raise InputError(f"--outdir {args.outdir} is in the case folder. Choose a folder outside it")
    if out.exists() and (not out.is_dir() or (any(out.iterdir()) and not args.force)):
        raise InputError(f"--outdir {args.outdir} is not empty. Choose a new folder or give --force")
    arrays_dir = args.outdir / "arrays"
    arrays_dir.mkdir(parents=True, exist_ok=True)
    column_full = np.broadcast_to(column_oil.T[None, :, :], (nz, ny, nx)).reshape(-1)
    rank_full = np.zeros((nz, ny, nx))
    for c in candidates:
        rank_full[:, c.j - 1, c.i - 1] = c.rank
    files = {}
    for name, values in (("MOBOIL", full), ("MOBCOL", column_full), ("CANDID", rank_full.reshape(-1))):
        files[name] = str(grdecl.write_grdecl(arrays_dir / f"{name}.grdecl", name, values.tolist()))
    with (args.outdir / "columns.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["i", "j", "mobile_oil_rm3"])
        for i in range(nx):
            for j in range(ny):
                if column_oil[i, j] > 0:
                    writer.writerow([i + 1, j + 1, f"{column_oil[i, j]:.6g}"])
    with (args.outdir / "candidates.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Candidate.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(c) for c in candidates)
    total = float(mobile.sum())
    summary = {
        "schema_version": SCHEMA_VERSION,
        "case": str(base),
        "date": date,
        "step": index,
        "dims": list(dims),
        "sor": args.sor,
        "min_distance": args.min_distance,
        "radius": args.radius,
        "mobile_oil_rm3": total,
        "wells": sorted(wells),
        "avoid": [e[0] for e in extra],
        "candidates": [asdict(c) for c in candidates],
        "arrays": files,
        "rule": "Screening only: mobile oil in place, not producible volume. No pressure, no flow paths.",
    }
    if args.view_case:
        k = 1
        spec = {
            "case": str(args.view_case),
            "out": str(args.outdir / "views"),
            "title": f"Remaining oil {date}",
            "arrays": [{"name": n, "file": f} for n, f in files.items()],
            "views": [
                {"name": "mobcol", "property": "INPUT_PROPERTY:MOBCOL", "range": {"k": [k, k]}},
                {"name": "candidates", "property": "INPUT_PROPERTY:CANDID", "range": {"k": [k, k]}},
                {"name": "soil", "property": "DYNAMIC_NATIVE:SOIL", "time_step": date},
            ],
            "notes": [
                "MOBCOL: mobile oil of the column (rm3), the same in every layer",
                "CANDID: candidate rank, 0 elsewhere",
            ],
        }
        (args.outdir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        summary["spec"] = str(args.outdir / "spec.json")
    (args.outdir / "remaining_oil.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        f"# Remaining oil at {date} (step {index})",
        "",
        f"Mobile oil in place: {total:.4g} rm3 (SOR {args.sor}).",
        "",
        f"| Rank | I | J | Mobile oil within {args.radius} cells (rm3) | Share | Nearest | Distance (cells) |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {c.rank} | {c.i} | {c.j} | {c.mobile_oil_rm3:.4g} | {c.share_of_total:.2%} | {c.nearest_well} | "
        f"{c.distance_cells} |"
        for c in candidates
    ] or ["| - | | | | | | |"]
    lines += ["", summary["rule"]]
    (args.outdir / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"candidates": len(candidates), "outdir": str(args.outdir)}))
    return 0 if candidates else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case", required=True, type=Path, help="BASE or BASE.EGRID (with INIT and UNRST)")
    parser.add_argument("--step", default="last", help="Report step: an index, a date YYYY-MM-DD, or last")
    parser.add_argument(
        "--completions", type=Path, help="connections.csv (or its folder) of completion_in_grid.py"
    )
    parser.add_argument("--avoid", action="append", help="NAME=I,J of a planned well. Repeat")
    parser.add_argument("--sor", type=float, default=0.0, help="Residual oil saturation to subtract")
    parser.add_argument("--min-distance", type=int, default=5, help="Cells from every well and candidate")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument(
        "--radius", type=int, default=3, help="Drainage window half-width in cells (0: one column)"
    )
    parser.add_argument("--min-share", type=float, default=0.0, help="Stop below this share of the total")
    parser.add_argument("--view-case", type=Path, help="EGRID to draw on. Writes spec.json")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if not (0 <= args.sor < 1) or args.min_distance < 0 or args.count < 1 or args.radius < 0:
        print(
            "ERROR: --sor must be in [0, 1), --min-distance >= 0, --radius >= 0, --count >= 1",
            file=sys.stderr,
        )
        return 2
    try:
        import numpy  # noqa: F401
        import resdata  # noqa: F401
    except ImportError as exc:
        print(f"ERROR: {exc.name} is not installed. Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        return run(args)
    except (InputError, grdecl.GrdeclError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
