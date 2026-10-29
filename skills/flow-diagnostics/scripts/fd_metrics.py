#!/usr/bin/env python3
"""Well allocation and F-Phi from the flow diagnostics arrays of fd_export.py, with resdata for PORV.

Allocation (inferred): for each producer, the PORV-weighted mean of each injector fraction over the
producer's open connection cells (connections.csv of grid-property-qc). It shows which injectors
support a producer. It is not the ResInsight well allocation plot, which uses the connection fluxes.
F-Phi: sort the active cells with a residence time by that time; Phi = cumulative PORV share,
F = cumulative share of PORV / time (the flow capacity). Lorenz coefficient = 2 * (area under F - 0.5):
0 is a uniform sweep, near 1 is flow in a few fast paths.
Writes allocation.csv, fphi.csv, fd_metrics.json, report.md. Exit 0, or 2 when an input is missing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "resinsight-views" / "scripts"))

import grdecl  # noqa: E402


class InputError(Exception):
    """Exit 2."""


def load_porv(base: Path):
    import numpy as np
    from resdata.resfile import ResdataFile

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    init = Path(f"{base}.INIT")
    if not init.is_file():
        raise InputError(f"missing {init}")
    return np.asarray(ResdataFile(str(init))["PORV"][0].numpy_copy(), dtype=float)


def fphi(restime, porv, points: int = 101):
    """F and Phi on `points` Phi values, and the Lorenz coefficient. Cells need PORV > 0 and time > 0."""
    import numpy as np

    keep = (porv > 0) & np.isfinite(restime) & (restime > 0)
    if keep.sum() < 2:
        raise InputError("fewer than 2 cells have a residence time and pore volume")
    order = np.argsort(restime[keep])
    pv = porv[keep][order]
    flow = pv / restime[keep][order]
    phi = np.concatenate([[0.0], np.cumsum(pv) / pv.sum()])
    f = np.concatenate([[0.0], np.cumsum(flow) / flow.sum()])
    area = float(np.sum((f[1:] + f[:-1]) * np.diff(phi)) / 2)  # trapezoid rule; np.trapezoid needs numpy 2
    lorenz = 2.0 * (area - 0.5)
    grid = np.linspace(0, 1, points)
    return grid, np.interp(grid, phi, f), float(lorenz), int(keep.sum())


def allocation(arrays: dict, connections: Path, porv, dims) -> list[dict]:
    import numpy as np

    nx, ny, _ = dims
    cells: dict[str, set[int]] = {}
    with connections.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("flag") in ("COMP-INACTIVE", "COMP-OUTSIDE") or row.get("status", "OPEN") != "OPEN":
                continue
            g = (int(row["i"]) - 1) + nx * ((int(row["j"]) - 1) + ny * (int(row["k"]) - 1))
            cells.setdefault(row["well"], set()).add(g)
    producers = [item["well"] for item in arrays.values() if item["role"] == "producer"]
    injectors = {key: item["well"] for key, item in arrays.items() if item["role"] == "injector"}
    rows = []
    for producer in producers:
        index = np.array(sorted(cells.get(producer, ())), dtype=int)
        if index.size == 0:
            rows.append(
                {"producer": producer, "injector": "", "share": None, "note": "no open connection cell"}
            )
            continue
        weights = porv[index]
        shares = {
            well: float(np.average(np.clip(arrays[key]["values"][index], 0, 1), weights=weights))
            for key, well in injectors.items()
        }
        total = sum(shares.values())
        for well, share in sorted(shares.items(), key=lambda item: -item[1]):
            rows.append(
                {
                    "producer": producer,
                    "injector": well,
                    "share": round(share, 4),
                    "note": "" if total > 0.5 else f"injectors explain {total:.0%} of the connection cells",
                }
            )
    return rows


def run(args: argparse.Namespace) -> int:
    import numpy as np

    manifest = json.loads((args.fd_dir / "fd_export.json").read_text())
    base = Path(manifest["case"])
    if base.resolve().parent in [args.outdir.resolve(), *args.outdir.resolve().parents]:
        raise InputError(f"--outdir {args.outdir} is in the case folder. Choose a folder outside it")
    porv = load_porv(base)
    for item in manifest["arrays"].values():
        _, values = grdecl.read_grdecl(Path(item["file"]), expected=len(porv))
        item["values"] = np.asarray(values, dtype=float)
    if "RESTIME" not in manifest["arrays"]:
        raise InputError("fd_export.json has no RESTIME. Rerun fd_export.py and read its failed list")
    from resdata.grid import Grid

    dims = Grid(f"{base}.EGRID").get_dims()[:3]
    phi, f, lorenz, n_cells = fphi(manifest["arrays"]["RESTIME"]["values"], porv)
    rows = allocation(manifest["arrays"], args.completions, porv, dims) if args.completions else []
    args.outdir.mkdir(parents=True, exist_ok=True)
    with (args.outdir / "fphi.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["phi", "f"])
        writer.writerows([[f"{a:.4f}", f"{b:.6f}"] for a, b in zip(phi, f, strict=True)])
    if rows:
        with (args.outdir / "allocation.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    result = {
        "case": str(base),
        "step": manifest["step"],
        "date": manifest.get("date"),
        "lorenz": round(lorenz, 4),
        "fphi_cells": n_cells,
        "f_at_phi_0_2": round(float(np.interp(0.2, phi, f)), 4),
        "allocation": "inferred: PORV-weighted mean injector fraction in the producer connection cells",
        "source": "flow diagnostics arrays from ResInsight; PORV and connection cells from resdata",
    }
    (args.outdir / "fd_metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        f"# Flow diagnostics at {result['date']} (step {result['step']})",
        "",
        f"- Lorenz coefficient: {lorenz:.3f} over {n_cells} cells (0 uniform, 1 a few fast paths).",
        f"- The fastest 20% of the pore volume carries {result['f_at_phi_0_2']:.0%} of the flow.",
        "",
    ]
    if rows:
        lines += [
            "## Injector support per producer (inferred)",
            "",
            "| Producer | Injector | Share |",
            "|---|---|---|",
        ]
        for producer in dict.fromkeys(r["producer"] for r in rows):
            mine = [r for r in rows if r["producer"] == producer and r["share"]]
            lines += [
                f"| {producer} | {r['injector']} | {r['share']:.0%} |"
                for r in mine
                if r["share"] >= args.min_share
            ]
    (args.outdir / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fd-dir", required=True, type=Path, help="--outdir of fd_export.py")
    parser.add_argument("--completions", type=Path, help="connections.csv of completion_in_grid.py")
    parser.add_argument("--min-share", type=float, default=0.05, help="Hide smaller shares in report.md")
    parser.add_argument("--outdir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.completions and args.completions.is_dir():
        args.completions = args.completions / "connections.csv"
    for path in [args.fd_dir / "fd_export.json"] + ([args.completions] if args.completions else []):
        if not path.is_file():
            print(f"ERROR: missing {path}", file=sys.stderr)
            return 2
    try:
        import numpy  # noqa: F401
        import resdata  # noqa: F401
    except ImportError as exc:
        print(f"ERROR: {exc.name} is not installed. Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        return run(args)
    except (InputError, grdecl.GrdeclError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
