#!/usr/bin/env python3
"""Compare a grid property between a prior and a posterior ensemble, cell by cell, with resdata.

For each property (default PORO,PERMX) and each active cell:
  prior / posterior mean, standard deviation, P10, P90 over the realizations (PERM* in log10 space),
  SDRATIO = posterior sd / prior sd   (below 1: the update reduced the spread; near 0: collapse),
  SHIFT   = (posterior mean - prior mean) / prior sd   (the move in prior standard deviations).
Outputs in --outdir: fields.json (findings, notes, per-property summary), per_layer.csv, report.md,
arrays/*.grdecl (whole grid, 0 in inactive cells) and spec.json for resinsight-views render_views.py.
Cells without prior spread have no SDRATIO or SHIFT: they are 0 in the arrays, out of the statistics,
and counted in the notes.
Exit 0 no warning finding, 1 warning findings, 2 input missing or grids that do not match.
All numbers come from resdata. ResInsight only draws the arrays.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "stage-contract" / "scripts"))
sys.path.insert(0, str(HERE.parents[2] / "resinsight-views" / "scripts"))

import grdecl  # noqa: E402
import runpath_lib  # noqa: E402

SCHEMA_VERSION = 1
Severity = Literal["warning", "info"]
TAGS = {"PRM": "prior mean", "POM": "posterior mean", "SDR": "sd ratio", "SHF": "shift in prior sd"}


class InputError(Exception):
    """Missing input or grids that do not match. Exit 2."""


@dataclass(frozen=True)
class Limits:
    collapse_ratio: float = 0.2  # SDRATIO below this is a collapsed cell
    collapse_fraction: float = 0.5  # warn when more active cells than this are collapsed
    shift_large: float = 2.0  # |SHIFT| above this is a large move
    shift_fraction: float = 0.1  # warn when more active cells than this moved a lot
    min_members: int = 10  # fewer members: P10/P90 are rough (info)
    no_update_shift: float = 0.05


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    property: str
    message: str
    value: float


@dataclass
class Stack:
    """One ensemble of one case layout: realization numbers, dims, active map, arrays per property."""

    label: str
    realizations: list[int]
    dims: tuple[int, int, int]
    active_global: Any
    values: dict[str, Any] = field(default_factory=dict)  # property -> (n_real, n_active)


def init_path(member: runpath_lib.Member) -> Path:
    assert member.data_file is not None
    return member.data_file.with_suffix(".INIT")


def load_stack(label: str, pattern: str, eclbase: str | None, properties: list[str], expected) -> Stack:
    import numpy as np
    from resdata.grid import Grid
    from resdata.resfile import ResdataFile

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    ensemble = runpath_lib.read_ensemble(pattern, eclbase, expected)
    if ensemble.dropped or ensemble.missing:
        raise InputError(f"{label}: " + "; ".join(ensemble.problems()))
    if len(ensemble.found) < 2:
        raise InputError(f"{label}: {len(ensemble.found)} realization(s) match {pattern}. Need 2 or more")
    dims = actnum = None
    columns: dict[str, list] = {name: [] for name in properties}
    for member in ensemble.found:
        egrid, init = member.data_file.with_suffix(".EGRID"), init_path(member)
        for path in (egrid, init):
            if not path.is_file():
                raise InputError(f"{label} realization {member.realization}: missing {path}")
        grid = Grid(str(egrid))
        nx, ny, nz, _ = grid.get_dims()
        mask = np.asarray(grid.export_actnum().numpy_copy()) & 1
        if dims is None:
            dims, actnum = (nx, ny, nz), mask
        elif (nx, ny, nz) != dims or not np.array_equal(mask, actnum):
            raise InputError(
                f"{label} realization {member.realization}: the grid or ACTNUM differs from realization "
                f"{ensemble.found[0].realization}. Cell-by-cell statistics need one grid"
            )
        file = ResdataFile(str(init))
        for name in properties:
            if name not in file:
                raise InputError(f"{label} realization {member.realization}: {init.name} has no {name}")
            columns[name].append(np.asarray(file[name][0].numpy_copy(), dtype=float))
    active_global = np.flatnonzero(actnum)
    stack = Stack(label, [m.realization for m in ensemble.found], dims, active_global)
    for name, rows in columns.items():
        array = np.vstack(rows)
        if array.shape[1] != len(active_global):
            raise InputError(
                f"{label}: {name} has {array.shape[1]} values, the grid has {len(active_global)} active"
            )
        stack.values[name] = array
    return stack


def transform(name: str, array):
    """log10 for permeability; a floor of 1e-6 mD keeps zero cells finite."""
    import numpy as np

    return np.log10(np.maximum(array, 1e-6)) if name.upper().startswith("PERM") else array


def cell_stats(array) -> dict[str, Any]:
    import numpy as np

    return {
        "mean": array.mean(axis=0),
        "sd": array.std(axis=0, ddof=1),
        "p10": np.percentile(array, 10, axis=0),
        "p90": np.percentile(array, 90, axis=0),
    }


def compare(prior: Stack, posterior: Stack, name: str) -> dict[str, Any]:
    import numpy as np

    a, b = transform(name, prior.values[name]), transform(name, posterior.values[name])
    sa, sb = cell_stats(a), cell_stats(b)
    spread = sa["sd"] > 1e-12
    ratio = np.where(spread, sb["sd"] / np.where(spread, sa["sd"], 1.0), np.nan)
    shift = np.where(spread, (sb["mean"] - sa["mean"]) / np.where(spread, sa["sd"], 1.0), np.nan)
    return {"prior": sa, "posterior": sb, "SDR": ratio, "SHF": shift, "spread": spread}


def findings_for(name: str, result: dict, limits: Limits, n_prior: int, n_post: int) -> list[Finding]:
    import numpy as np

    out: list[Finding] = []
    ratio, shift, spread = result["SDR"], result["SHF"], result["spread"]
    n = int(spread.sum())
    if n == 0:
        return [Finding("AHM-FIELD-NO-SPREAD", "info", name, "the prior has no spread in any cell", 0.0)]
    collapsed = float(np.mean(ratio[spread] < limits.collapse_ratio))
    moved = float(np.mean(np.abs(shift[spread]) > limits.shift_large))
    if collapsed > limits.collapse_fraction:
        out.append(
            Finding(
                "AHM-FIELD-COLLAPSE",
                "warning",
                name,
                f"{collapsed:.0%} of the cells with prior spread have SDRATIO < {limits.collapse_ratio}",
                collapsed,
            )
        )
    if moved > limits.shift_fraction:
        out.append(
            Finding(
                "AHM-FIELD-SHIFT-LARGE",
                "warning",
                name,
                f"{moved:.0%} of the cells moved more than {limits.shift_large} prior sd",
                moved,
            )
        )
    if np.nanmax(np.abs(shift[spread])) < limits.no_update_shift and np.allclose(ratio[spread], 1, atol=0.05):
        out.append(
            Finding("AHM-FIELD-NO-UPDATE", "info", name, "the posterior equals the prior in every cell", 0.0)
        )
    if min(n_prior, n_post) < limits.min_members:
        out.append(
            Finding(
                "AHM-FIELD-SMALL-ENSEMBLE",
                "info",
                name,
                f"{min(n_prior, n_post)} realizations: P10 and P90 are rough",
                float(min(n_prior, n_post)),
            )
        )
    return out


def per_layer(stack: Stack, name: str, result: dict, limits: Limits) -> list[dict[str, Any]]:
    import numpy as np

    nx, ny, _ = stack.dims
    layer = stack.active_global // (nx * ny) + 1
    rows = []
    for k in np.unique(layer):
        cells = layer == k
        keep = cells & result["spread"]
        rows.append(
            {
                "property": name,
                "k": int(k),
                "n_active": int(cells.sum()),
                "prior_mean": float(result["prior"]["mean"][cells].mean()),
                "posterior_mean": float(result["posterior"]["mean"][cells].mean()),
                "sdratio_median": float(np.median(result["SDR"][keep])) if keep.any() else None,
                "abs_shift_mean": float(np.mean(np.abs(result["SHF"][keep]))) if keep.any() else None,
                "collapsed_fraction": float(np.mean(result["SDR"][keep] < limits.collapse_ratio))
                if keep.any()
                else None,
            }
        )
    return rows


def keyword(name: str, tag: str) -> str:
    return f"{name[:5]}{tag}"


def check_case(case: Path, stack: Stack) -> None:
    """The --case grid must be the grid of the ensembles: the arrays have one value per cell of it."""
    import numpy as np
    from resdata.grid import Grid

    if not case.is_file():
        raise InputError(f"--case {case} is not a file")
    grid = Grid(str(case))
    actnum = np.asarray(grid.export_actnum().numpy_copy()) & 1
    if tuple(grid.get_dims()[:3]) != stack.dims or not np.array_equal(
        np.flatnonzero(actnum), stack.active_global
    ):
        raise InputError(f"--case {case}: the grid or ACTNUM differs from the ensembles")


def whole_grid(stack: Stack, active_values) -> list[float]:
    import numpy as np

    nx, ny, nz = stack.dims
    full = np.zeros(nx * ny * nz)
    full[stack.active_global] = np.nan_to_num(active_values, nan=0.0)
    return full.tolist()


def write_report(path: Path, summary: dict, findings: list[Finding], layers: list[dict]) -> None:
    lines = [
        f"# Prior and posterior fields ({summary['prior']['n']} and {summary['posterior']['n']} realizations)",
        "",
    ]
    lines += [
        "| Property | Space | Prior sd (median) | SDRATIO median | mean abs SHIFT | Layer for the views |",
        "|---|---|---|---|---|---|",
    ]
    for name, item in summary["properties"].items():
        sdr, shf = (
            ("-" if item[key] is None else f"{item[key]:.3f}") for key in ("sdratio_median", "abs_shift_mean")
        )
        lines.append(
            f"| {name} | {item['space']} | {item['prior_sd_median']:.4g} | {sdr} | {shf} | {item['view_layer']} |"
        )
    lines += ["", "## Findings", ""]
    lines += [f"- {f.code} ({f.severity}, {f.property}): {f.message}" for f in findings] or ["No findings."]
    lines += ["", "## Notes", ""] + [f"- {note}" for note in summary["notes"]]
    lines += [
        "",
        "## Per layer: see per_layer.csv",
        "",
        "SDRATIO below 1: the update reduced the spread. SHIFT: the move of the mean in prior sd.",
    ]
    path.write_text("\n".join(lines) + "\n")


def run(args: argparse.Namespace) -> int:
    import numpy as np

    properties = [p.strip().upper() for p in args.properties.split(",") if p.strip()]
    keywords = [keyword(name, tag) for name in properties for tag in TAGS]
    if not properties or len(set(keywords)) != len(keywords):
        raise InputError(
            f"--properties {args.properties!r}: give one or more properties with different first 5 letters"
        )
    outdir = args.outdir.resolve()  # spec.json needs absolute paths
    if outdir.exists() and not outdir.is_dir():
        raise InputError(f"--outdir {outdir} is a file")
    if outdir.exists() and any(outdir.iterdir()) and not args.force:
        raise InputError(f"--outdir {outdir} is not empty. Choose a new folder or give --force")
    expected = runpath_lib.parse_realizations(args.realizations) if args.realizations else None
    prior = load_stack("prior", args.prior, args.eclbase, properties, expected)
    posterior = load_stack("posterior", args.posterior, args.eclbase, properties, expected)
    if prior.dims != posterior.dims or not np.array_equal(prior.active_global, posterior.active_global):
        raise InputError("the prior and the posterior grids differ. Cell-by-cell statistics need one grid")
    if args.layer is not None and not 1 <= args.layer <= prior.dims[2]:
        raise InputError(f"--layer {args.layer} is outside 1..{prior.dims[2]}")
    case = args.case.resolve() if args.case else None
    if case:
        check_case(case, prior)
    (outdir / "arrays").mkdir(parents=True, exist_ok=True)
    limits = Limits()
    findings: list[Finding] = []
    layers: list[dict] = []
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "prior": {"pattern": args.prior, "n": len(prior.realizations), "realizations": prior.realizations},
        "posterior": {
            "pattern": args.posterior,
            "n": len(posterior.realizations),
            "realizations": posterior.realizations,
        },
        "dims": list(prior.dims),
        "n_active": int(len(prior.active_global)),
        "limits": asdict(limits),
        "properties": {},
        "arrays": {},
        "notes": [],
    }
    views, arrays = [], []
    for name in properties:
        result = compare(prior, posterior, name)
        findings += findings_for(name, result, limits, len(prior.realizations), len(posterior.realizations))
        rows = per_layer(prior, name, result, limits)
        layers += rows
        scored = [r for r in rows if r["abs_shift_mean"] is not None]
        k = args.layer or (max(scored, key=lambda r: r["abs_shift_mean"])["k"] if scored else 1)
        keep = result["spread"]
        if not keep.all():
            summary["notes"].append(
                f"{name}: {int((~keep).sum())} of {keep.size} active cells have no prior spread. They have no SDRATIO "
                "or SHIFT: 0 in the arrays, out of the statistics and the findings"
            )
        summary["properties"][name] = {
            "space": "log10" if name.startswith("PERM") else "linear",
            "prior_sd_median": float(np.median(result["prior"]["sd"])),
            "sdratio_median": float(np.median(result["SDR"][keep])) if keep.any() else None,
            "abs_shift_mean": float(np.mean(np.abs(result["SHF"][keep]))) if keep.any() else None,
            "view_layer": k,
            "view_layer_rule": "--layer" if args.layer else "the layer with the largest mean abs SHIFT",
        }
        sources = {
            "PRM": result["prior"]["mean"],
            "POM": result["posterior"]["mean"],
            "SDR": result["SDR"],
            "SHF": result["SHF"],
        }
        for tag, values in sources.items():
            kw = keyword(name, tag)
            path = outdir / "arrays" / f"{kw}.grdecl"
            grdecl.write_grdecl(path, kw, whole_grid(prior, values))
            arrays.append({"name": kw, "file": str(path)})
            summary["arrays"][kw] = f"{name} {TAGS[tag]}" + (
                " (log10)" if name.startswith("PERM") and tag in ("PRM", "POM") else ""
            )
            views.append(
                {
                    "name": f"{name.lower()}_{tag.lower()}_k{k}",
                    "property": f"INPUT_PROPERTY:{kw}",
                    "range": {"k": [k, k]},
                }
            )
    if case:
        spec = {
            "case": str(case),
            "out": str(outdir / "views"),
            "title": "Prior and posterior fields",
            "arrays": arrays,
            "views": views,
            "notes": [f"{kw}: {text}" for kw, text in summary["arrays"].items()] + summary["notes"],
        }
        (outdir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        summary["spec"] = str(outdir / "spec.json")
    summary["findings"] = [asdict(f) for f in findings]
    (outdir / "fields.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    with (outdir / "per_layer.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(layers[0]))
        writer.writeheader()
        writer.writerows(layers)
    write_report(outdir / "report.md", summary, findings, layers)
    print(json.dumps({"findings": len(findings), "outdir": str(outdir), "spec": summary.get("spec")}))
    return 1 if any(f.severity == "warning" for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--prior", required=True, help="Glob of the prior runpaths, e.g. '<root>/realization-*/iter-0'"
    )
    parser.add_argument("--posterior", required=True, help="Glob of the posterior runpaths")
    parser.add_argument(
        "--eclbase", help="ECLBASE, e.g. DROGON-<IENS>. Default: the one .DATA in the runpath model folder"
    )
    parser.add_argument("--realizations", help="Expected realizations, e.g. 0-15")
    parser.add_argument("--properties", default="PORO,PERMX")
    parser.add_argument(
        "--layer", type=int, help="Layer k for the views. Default: the layer with the largest mean abs SHIFT"
    )
    parser.add_argument(
        "--case", type=Path, help="EGRID to draw the arrays on (one realization). Writes spec.json"
    )
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        import numpy  # noqa: F401
        import resdata  # noqa: F401
    except ImportError as exc:
        print(f"ERROR: {exc.name} is not installed. Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        return run(args)
    except (InputError, ValueError, grdecl.GrdeclError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
