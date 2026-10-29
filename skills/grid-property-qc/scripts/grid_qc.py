#!/usr/bin/env python3
"""Check the grid properties of one simulator case (EGRID + INIT) with resdata. Read-only.

Usage:
    grid_qc.py --case <BASE or BASE.EGRID> --outdir <new dir> [--poro-max 0.5] [--permz-fraction 0.05]
               [--porv-ratio-min 0.5] [--porv-ratio-max 2.0] [--force]

Output in --outdir: grid_qc.json, per_layer.csv, per_region.csv, report.md.
Exit code: 0 = no error finding, 1 = at least one error finding, 2 = missing input.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from grid_lib import (
    REGION_ARRAYS,
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
QUANTILES = (0.0, 0.01, 0.1, 0.5, 0.9, 0.99, 1.0)


@dataclass(frozen=True)
class Limits:
    poro_max: float = 0.5
    ntg_min: float = 0.0
    ntg_max: float = 1.0
    permz_gt_permx_warn_fraction: float = 0.05
    porv_ratio_min: float = 0.5
    porv_ratio_max: float = 2.0


def check_finite(case: GridCase) -> list[Finding]:
    import numpy as np

    findings = []
    for name, values in case.arrays.items():
        bad = ~np.isfinite(values)
        if bad.any():
            findings.append(
                Finding(
                    "GRID-NAN", "error", f"{name} has NaN or inf values", int(bad.sum()), case.sample(bad)
                )
            )
    bad_global = ~np.isfinite(case.porv)
    if bad_global.any():
        cells = [case.ijk(g) for g in np.flatnonzero(bad_global)[:20]]
        findings.append(
            Finding("GRID-NAN", "error", "PORV has NaN or inf values", int(bad_global.sum()), cells)
        )
    return findings


def check_ranges(case: GridCase, limits: Limits) -> list[Finding]:
    import numpy as np

    findings = []
    porv = case.porv[case.active_global]
    bad = porv <= 0
    if bad.any():
        findings.append(
            Finding(
                "GRID-ACTIVE-ZERO-PORV",
                "error",
                "active cells with PORV <= 0",
                int(bad.sum()),
                case.sample(bad),
            )
        )
    poro = case.arrays["PORO"]
    bad = (poro < 0) | (poro > limits.poro_max)
    if bad.any():
        findings.append(
            Finding(
                "GRID-RANGE-PORO",
                "error",
                f"PORO outside [0, {limits.poro_max}]; min {np.nanmin(poro):.4g}, max {np.nanmax(poro):.4g}",
                int(bad.sum()),
                case.sample(bad),
            )
        )
    if "NTG" in case.arrays:
        ntg = case.arrays["NTG"]
        bad = (ntg < limits.ntg_min) | (ntg > limits.ntg_max)
        if bad.any():
            findings.append(
                Finding(
                    "GRID-RANGE-NTG",
                    "error",
                    f"NTG outside [{limits.ntg_min}, {limits.ntg_max}]; min {np.nanmin(ntg):.4g}, max {np.nanmax(ntg):.4g}",
                    int(bad.sum()),
                    case.sample(bad),
                )
            )
    perms = {n: case.arrays[n] for n in ("PERMX", "PERMY", "PERMZ") if n in case.arrays}
    if perms:
        negative = {n: v < 0 for n, v in perms.items()}
        bad = np.logical_or.reduce(list(negative.values()))
        if bad.any():
            counts = ", ".join(f"{n} {int(m.sum())}" for n, m in negative.items() if m.any())
            findings.append(
                Finding(
                    "GRID-NEG-PERM",
                    "error",
                    f"negative permeability: {counts}",
                    int(bad.sum()),
                    case.sample(bad),
                )
            )
    if "PERMX" in perms and "PERMZ" in perms:
        permx, permz = perms["PERMX"], perms["PERMZ"]
        valid = np.isfinite(permx) & np.isfinite(permz)
        higher = valid & (permz > permx * (1 + 1e-6))
        fraction = higher.sum() / max(int(valid.sum()), 1)
        if fraction > limits.permz_gt_permx_warn_fraction:
            findings.append(
                Finding(
                    "GRID-PERMZ-GT-PERMX",
                    "warning",
                    f"PERMZ > PERMX in {fraction:.1%} of active cells (limit {limits.permz_gt_permx_warn_fraction:.1%})",
                    int(higher.sum()),
                    case.sample(higher),
                )
            )
    return findings


def check_regions(case: GridCase) -> list[Finding]:
    import numpy as np

    findings = []
    for name in REGION_ARRAYS:
        if name not in case.arrays:
            continue
        values = case.arrays[name]
        bad = values <= 0
        if bad.any():
            findings.append(
                Finding(
                    "GRID-REGION-ZERO",
                    "error",
                    f"{name} is 0 or negative in active cells",
                    int(bad.sum()),
                    case.sample(bad),
                )
            )
        top = int(values.max()) if len(values) else 0
        missing = sorted(set(range(1, top + 1)) - set(np.unique(values).tolist()))
        if missing:
            shown = ", ".join(map(str, missing[:20])) + (" ..." if len(missing) > 20 else "")
            findings.append(
                Finding(
                    "GRID-REGION-GAP",
                    "info",
                    f"{name} numbers 1..{top} with no active cell: {shown}",
                    len(missing),
                )
            )
    return findings


def porv_ratio(case: GridCase):
    """PORV / (bulk volume * PORO * NTG) for each active cell. inf where PORV > 0 and the bulk pore volume is 0.

    NaN where another check owns the cell: PORV <= 0, a negative bulk pore volume, or a NaN input.
    """
    import numpy as np

    ntg = case.arrays.get("NTG", np.ones(case.n_active))
    bulk_pore = case.volume * case.arrays["PORO"] * ntg
    porv = case.porv[case.active_global]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(bulk_pore == 0, np.inf, porv / bulk_pore)
    owned = ~np.isfinite(porv) | (porv <= 0) | ~np.isfinite(bulk_pore) | (bulk_pore < 0)
    return np.where(owned, np.nan, ratio)


def check_porv_vs_bulk(case: GridCase, limits: Limits, ratio) -> tuple[list[Finding], dict[str, float]]:
    import numpy as np

    finite = ratio[np.isfinite(ratio)]
    stats = (
        {f"p{round(q * 100):02d}": float(np.quantile(finite, q)) for q in QUANTILES} if len(finite) else {}
    )
    bad = (ratio < limits.porv_ratio_min) | (ratio > limits.porv_ratio_max)
    if not bad.any():
        return [], stats
    text = ", ".join(f"{k} {v:.3g}" for k, v in stats.items())
    message = (
        f"PORV / (bulk volume * PORO * NTG) outside [{limits.porv_ratio_min}, {limits.porv_ratio_max}]; "
        f"ratio quantiles: {text}"
    )
    return [Finding("GRID-PORV-VS-BULK", "warning", message, int(bad.sum()), case.sample(bad))], stats


def percentile(values, q: float) -> float | str:
    import numpy as np

    finite = values[np.isfinite(values)]
    return round(float(np.percentile(finite, q)), 6) if len(finite) else ""


def mean(values) -> float | str:
    import numpy as np

    finite = values[np.isfinite(values)]
    return round(float(finite.mean()), 6) if len(finite) else ""


def per_layer_rows(case: GridCase) -> list[dict]:
    import numpy as np

    nx, ny, nz = case.dims
    layer = case.active_global // (nx * ny)
    porv = case.porv[case.active_global]
    rows = []
    for k in range(nz):
        m = layer == k
        permx = case.arrays.get("PERMX", np.full(case.n_active, np.nan))[m]
        rows.append(
            {
                "k": k + 1,
                "n_active": int(m.sum()),
                "porv_sum": round(float(porv[m].sum()), 3),
                "poro_mean": mean(case.arrays["PORO"][m]),
                "permx_p10": percentile(permx, 10),
                "permx_p50": percentile(permx, 50),
                "permx_p90": percentile(permx, 90),
                "ntg_mean": mean(case.arrays["NTG"][m]) if "NTG" in case.arrays else "",
            }
        )
    return rows


def per_region_rows(case: GridCase) -> list[dict]:
    import numpy as np

    porv = case.porv[case.active_global]
    rows = []
    for name in REGION_ARRAYS:
        if name not in case.arrays:
            continue
        values = case.arrays[name]
        for region in np.unique(values).tolist():
            m = values == region
            rows.append(
                {
                    "array": name,
                    "region": region,
                    "n_active": int(m.sum()),
                    "porv_sum": round(float(porv[m].sum()), 3),
                    "poro_mean": mean(case.arrays["PORO"][m]),
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(case: GridCase, limits: Limits) -> tuple[list[Finding], list[str], dict]:
    notes = [
        f"{name} is not in the INIT file; its checks did not run"
        for name in ("NTG", "PERMX", "PERMY", "PERMZ")
        if name not in case.arrays
    ]
    notes += [f"{name} is not in the INIT file" for name in REGION_ARRAYS if name not in case.arrays]
    if "NTG" not in case.arrays:
        notes.append("GRID-PORV-VS-BULK uses NTG = 1")
    ratio = porv_ratio(case)
    findings = check_finite(case) + check_ranges(case, limits) + check_regions(case)
    porv_findings, stats = check_porv_vs_bulk(case, limits, ratio)
    return findings + porv_findings, notes, {"porv_ratio_quantiles": stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case", required=True, help="BASE or BASE.EGRID. BASE.INIT must be next to it")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--poro-max", type=float, default=Limits.poro_max)
    parser.add_argument("--permz-fraction", type=float, default=Limits.permz_gt_permx_warn_fraction)
    parser.add_argument("--porv-ratio-min", type=float, default=Limits.porv_ratio_min)
    parser.add_argument("--porv-ratio-max", type=float, default=Limits.porv_ratio_max)
    parser.add_argument("--force", action="store_true", help="Write into a folder that is not empty")
    args = parser.parse_args(argv)
    limits = Limits(
        poro_max=args.poro_max,
        permz_gt_permx_warn_fraction=args.permz_fraction,
        porv_ratio_min=args.porv_ratio_min,
        porv_ratio_max=args.porv_ratio_max,
    )
    missing = need_packages("numpy", "resdata")
    if missing:
        return fail(f"{missing} is not installed. Use the Komodo Python or install {missing}")
    try:
        case = load_case(args.case)
        prepare_outdir(args.outdir, case.base, args.force)
    except InputError as exc:
        return fail(str(exc))

    findings, notes, stats = run(case, limits)
    nx, ny, nz = case.dims
    write_json(
        args.outdir / "grid_qc.json",
        {
            "schema_version": SCHEMA_VERSION,
            "case": str(case.base),
            "dims": [nx, ny, nz],
            "n_active": case.n_active,
            "findings": finding_dicts(findings),
            "notes": notes,
            "limits": asdict(limits),
            "stats": stats,
        },
    )
    write_csv(
        args.outdir / "per_layer.csv",
        per_layer_rows(case),
        ["k", "n_active", "porv_sum", "poro_mean", "permx_p10", "permx_p50", "permx_p90", "ntg_mean"],
    )
    write_csv(
        args.outdir / "per_region.csv",
        per_region_rows(case),
        ["array", "region", "n_active", "porv_sum", "poro_mean"],
    )
    lines = [
        f"# Grid property QC: {case.base.name}",
        "",
        f"Grid {nx} x {ny} x {nz}, {case.n_active} active cells. Numbers come from resdata.",
        "Cells are (i, j, k), 1-based. P10 is the low value (numerical percentile).",
        "",
        *findings_markdown(findings),
    ]
    if notes:
        lines += ["", "## Notes", *[f"- {n}" for n in notes]]
    (args.outdir / "report.md").write_text("\n".join(lines) + "\n")
    errors = sum(f.severity == "error" for f in findings)
    print(
        f"grid_qc: {case.n_active} active cells, {len(findings)} findings, {errors} errors. Wrote {args.outdir}"
    )
    for f in findings:
        print(f"FINDING {f.code} {f.severity} count={f.count}: {f.message}")
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
