#!/usr/bin/env python3
"""Read the semeio AHM_ANALYSIS tables against a noise floor. Read-only. Standard library only.

AHM_ANALYSIS re-runs one ES update from the prior for each observation group alone, for all
groups, and for all groups minus one. ks.csv holds the two-sample Kolmogorov-Smirnov distance
between the prior and each of these posteriors, for each scalar parameter.

A KS value is not an influence. With N realizations, two samples of the same distribution give
a KS distance of about 1.36 * sqrt(2 / N) at the 5 % level (0.48 at N = 16). This script:

- computes the floor: the exact two-sample KS critical value c(alpha, n, m) from the count of
  lattice paths (the distribution of D under the null hypothesis), and the asymptotic value
  sqrt(-ln(alpha / 2) / 2) * sqrt((n + m) / (n m)) for reference. The floor is the exact value.
  The posterior of an ES update is a transform of the prior members, not an independent sample.
  The floor is a scale for the noise, not a test that the update is real.
  No empirical (shuffled-response) floor: it needs one ES update per shuffle in ERT.
- flags each parameter and group above the floor, and names the top group of each parameter
  (all groups that share the largest KS);
- finds the spurious-correlation signature: one group is the top group for at least --share
  of the parameters (and for at least --min-params parameters). A single well that "drives"
  porosity, fault multipliers and SCAL exponents at the same time is a correlation from a small
  ensemble, not an influence;
- makes the leave-one-out table: KS(All_obs) - KS(All_obs-<group>) for each parameter;
- optionally compares the top group of each parameter with the driver of ahm_overview.py
  (--overview-json). An unknown schema gives "not compared". It never fails the run.

Usage:
    ahm_influence.py --ahm-dir <AhmAnalysisJob> [--n 16] [--alpha 0.05] --out <dir>

Outputs in --out: influence.csv, influence.md, influence.json.
Exit code: 0 = no signature, 1 = the spurious-correlation signature fires, 2 = input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ALL = "All_obs"
LEAVE_OUT = "All_obs-"


class InputError(Exception):
    pass


def ks_sf(t: int, n: int, m: int) -> float:
    """P(n*m*D >= t) for the two-sample KS statistic D under the null hypothesis, exact."""
    row = [1] + [0] * m
    for j in range(1, m + 1):
        row[j] = row[j - 1] if abs(j * n) < t else 0
    for i in range(1, n + 1):
        row[0] = row[0] if i * m < t else 0
        for j in range(1, m + 1):
            row[j] = row[j] + row[j - 1] if abs(i * m - j * n) < t else 0
    total = math.comb(n + m, n)
    return (total - row[m]) / total


def ks_critical(alpha: float, n: int, m: int) -> float:
    """The smallest D with P(D >= d) <= alpha (exact)."""
    values = sorted({abs(i * m - j * n) for i in range(n + 1) for j in range(m + 1)} - {0})
    lo, hi = 0, len(values) - 1
    if ks_sf(values[hi], n, m) > alpha:
        return 1.0
    while lo < hi:
        mid = (lo + hi) // 2
        if ks_sf(values[mid], n, m) <= alpha:
            hi = mid
        else:
            lo = mid + 1
    return values[lo] / (n * m)


def ks_critical_asymptotic(alpha: float, n: int, m: int) -> float:
    return math.sqrt(-math.log(alpha / 2) / 2) * math.sqrt((n + m) / (n * m))


def read_matrix(path: Path) -> tuple[list[str], dict[str, dict[str, float]]]:
    """ks.csv: first column Parameters, one column per observation group."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2:
        raise InputError(f"{path}: no parameter rows")
    header = [h.strip() for h in rows[0]]
    columns = [h for h in header[1:] if h]
    matrix: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        values = {}
        for name, cell in zip(header[1:], row[1:], strict=False):
            if not name:
                continue
            try:
                values[name] = float(cell)
            except ValueError as error:
                raise InputError(f"{path}: {row[0]} / {name}: '{cell}' is not a number") from error
        missing = [c for c in columns if c not in values]
        if missing:
            raise InputError(f"{path}: {row[0]} has no value for {', '.join(missing[:5])}")
        matrix[row[0].strip()] = values
    if not matrix:
        raise InputError(f"{path}: no parameter rows")
    return columns, matrix


def read_one_row(path: Path) -> dict[str, str]:
    """misfit_obs_info.csv or active_obs_info.csv: a header and one data row."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2:
        return {}
    return {k.strip(): v for k, v in zip(rows[0][1:], rows[1][1:], strict=False) if k.strip()}


def count_prior_rows(path: Path) -> int | None:
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for row in list(csv.reader(handle))[1:] if row and row[0].strip())


def analyse(
    columns: list[str], matrix: dict[str, dict[str, float]], floor: float, share: float, min_params: int
) -> dict:
    groups = [c for c in columns if c != ALL and not c.startswith(LEAVE_OUT)]
    if not groups:
        raise InputError("ks.csv has no single-group column (only All_obs columns)")
    params = sorted(matrix)
    rows, per_param = [], {}
    for p in params:
        values = matrix[p]
        top = max(values[g] for g in groups)
        tops = [g for g in groups if values[g] == top] if top > 0 else []
        ks_all = values.get(ALL)
        drops = {}
        for g in groups:
            minus = values.get(LEAVE_OUT + g)
            drop = ks_all - minus if ks_all is not None and minus is not None else None
            drops[g] = drop
            rows.append(
                {
                    "parameter": p,
                    "group": g,
                    "ks": values[g],
                    "above_floor": values[g] >= floor,
                    "top": g in tops,
                    "ks_all": ks_all,
                    "ks_all_minus_group": minus,
                    "loo_drop": drop,
                }
            )
        known = {g: d for g, d in drops.items() if d is not None and d > 0}
        best_drop = max(known, key=lambda g: (known[g], g)) if known else None
        per_param[p] = {
            "max_ks": top,
            "top_groups": tops,
            "above_floor": top >= floor,
            "groups_above_floor": sum(values[g] >= floor for g in groups),
            "ks_all": ks_all,
            "ks_all_above_floor": ks_all >= floor if ks_all is not None else None,
            "largest_loo_drop": {"group": best_drop, "drop": known[best_drop]} if best_drop else None,
        }
    dominance = []
    for g in groups:
        led = [p for p in params if g in per_param[p]["top_groups"]]
        if led:
            dominance.append(
                {
                    "group": g,
                    "params": led,
                    "count": len(led),
                    "share": len(led) / len(params),
                    "above_floor": sum(matrix[p][g] >= floor for p in led),
                }
            )
    dominance.sort(key=lambda d: (-d["count"], d["group"]))
    fired = [d for d in dominance if d["share"] >= share and d["count"] >= min_params]
    return {
        "groups": groups,
        "params": params,
        "rows": rows,
        "per_param": per_param,
        "dominance": dominance,
        "signature": {
            "fired": bool(fired),
            "groups": fired,
            "share_threshold": share,
            "min_params": min_params,
            "message": (
                f"{fired[0]['group']} is the top group for {fired[0]['count']} of {len(params)} parameters: "
                "dominance hypothesis, not established influence"
                if fired
                else "no single group dominates"
            ),
        },
        "leave_one_out_available": any(r["loo_drop"] is not None for r in rows),
    }


def family(name: str) -> str:
    """WBHP_A2, WBHP:A2 and WBHP give WBHP; GEN:RFT_R_A2 and RFT_R_A2 give RFT."""
    base = name.removeprefix("GEN:").split(":")[0]
    return base.split("_")[0]


def cross_check(path: Path, per_param: dict) -> dict:
    """Compare the top group family per parameter with the ahm_overview.py driver_group."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        drivers = {
            name: entry["driver_group"]
            for name, entry in doc["influence"]["parameters"].items()
            if isinstance(entry, dict) and entry.get("driver_group")
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {
            "status": "not compared",
            "reason": f"unknown overview schema ({type(error).__name__}: {error})",
        }
    by_short = {name.split(":")[-1]: driver for name, driver in drivers.items()}
    items = []
    for p, info in per_param.items():
        driver = drivers.get(p, by_short.get(p.split(":")[-1]))
        if driver is None or not info["top_groups"]:
            continue
        tops = sorted({family(g) for g in info["top_groups"]})
        items.append(
            {
                "parameter": p,
                "overview": family(driver),
                "ahm_analysis": tops,
                "agree": family(driver) in tops,
            }
        )
    if not items:
        return {"status": "not compared", "reason": "no parameter in common with a driver group"}
    agree = sum(i["agree"] for i in items)
    return {
        "status": "compared",
        "level": "observation family (WBHP, WOPR, RFT, ...)",
        "compared": len(items),
        "agree": agree,
        "percent": round(100 * agree / len(items), 1),
        "items": items,
    }


def fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def markdown(summary: dict, result: dict) -> str:
    f = summary["floor"]
    lines = [
        "# AHM_ANALYSIS against a noise floor",
        "",
        f"Noise floor: KS >= {f['ks_critical']:.3f} (exact two-sample, alpha {f['alpha']}, n = {f['n']}, m = {f['m']}; "
        f"asymptotic {f['ks_critical_asymptotic']:.3f}). A KS below the floor is a reference scale, not proof of noise.",
        f"{len(result['params'])} scalar parameters, {len(result['groups'])} observation groups. "
        f"{summary['params_above_floor']} parameters have a group above the floor.",
        "",
    ]
    sig = result["signature"]
    if sig["fired"]:
        lines += [f"**WARNING: {sig['message']}.**", ""]
        for d in sig["groups"]:
            lines.append(
                f"- {d['group']}: top for {d['count']} of {len(result['params'])} ({d['share']:.0%}), "
                f"{d['above_floor']} of them above the floor: {', '.join(d['params'])}"
            )
        lines += [
            "",
            "Check whether these parameters can respond to this group. Use localization or more realizations.",
            "",
        ]
    else:
        lines += [f"Spurious-correlation signature: not found ({sig['message']}).", ""]
    lines += [
        "## Parameters, ranked by the largest single-group KS",
        "",
        "| Parameter | Top group | KS | Above floor | Groups above floor | KS All_obs | Largest leave-one-out drop |",
        "|---|---|---|---|---|---|---|",
    ]
    ranked = sorted(result["per_param"].items(), key=lambda kv: (-kv[1]["max_ks"], kv[0]))
    for p, info in ranked:
        loo = info["largest_loo_drop"]
        loo_text = f"{loo['group']} {loo['drop']:+.3f}" if loo else ""
        lines.append(
            f"| {p} | {', '.join(info['top_groups']) or '-'} | {info['max_ks']:.3f} | "
            f"{'yes' if info['above_floor'] else 'no'} | {info['groups_above_floor']} | {fmt(info['ks_all'])} | {loo_text} |"
        )
    lines += [
        "",
        "## Top group counts",
        "",
        "| Group | Top for | Share | Above floor | Misfit |",
        "|---|---|---|---|---|",
    ]
    misfit = summary.get("misfit", {})
    for d in result["dominance"]:
        lines.append(
            f"| {d['group']} | {d['count']} | {d['share']:.0%} | {d['above_floor']} | {misfit.get(d['group'], '')} |"
        )
    cc = summary.get("cross_check")
    if cc:
        lines += ["", "## Cross-check with ahm_overview", ""]
        if cc["status"] == "compared":
            lines.append(
                f"Agreement on the top family: {cc['agree']} of {cc['compared']} ({cc['percent']} %)."
            )
        else:
            lines.append(f"Not compared: {cc['reason']}.")
    lines += [
        "",
        "Limits: scalar parameters only (semeio skips FIELD parameters). One ES step from the prior, "
        "not the ES-MDA chain. The floor is analytic; no shuffled-response floor.",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    ahm_dir = Path(args.ahm_dir)
    ks_file = ahm_dir / "ks.csv"
    if not ks_file.is_file():
        raise InputError(
            f"{ks_file} not found (give the AhmAnalysisJob folder or the run_ahm_analysis.py output)"
        )
    if not 0 < args.alpha < 1:
        raise InputError("--alpha must be between 0 and 1")
    prior_rows = count_prior_rows(ahm_dir / "prior.csv")
    n = args.n if args.n is not None else prior_rows
    if n is None:
        raise InputError("give --n: there is no prior.csv to count the realizations")
    if prior_rows is not None and prior_rows != n:
        raise InputError(f"--n {n} does not match prior.csv ({prior_rows} realizations)")
    if n < 2:
        raise InputError("--n must be 2 or more")
    columns, matrix = read_matrix(ks_file)
    floor = ks_critical(args.alpha, n, n)
    result = analyse(columns, matrix, floor, args.share, args.min_params)
    misfit = read_one_row(ahm_dir / "misfit_obs_info.csv")
    active = read_one_row(ahm_dir / "active_obs_info.csv")
    summary = {
        "floor": {
            "alpha": args.alpha,
            "n": n,
            "m": n,
            "ks_critical": floor,
            "ks_critical_asymptotic": ks_critical_asymptotic(args.alpha, n, n),
            "method": "exact two-sample KS null distribution (lattice path count); asymptotic for reference",
            "empirical": "not computed: a shuffled-response floor needs one ES update per shuffle in ERT",
        },
        "params": len(result["params"]),
        "groups": len(result["groups"]),
        "params_above_floor": sum(i["above_floor"] for i in result["per_param"].values()),
        "signature": result["signature"],
        "dominance": result["dominance"],
        "per_param": result["per_param"],
        "leave_one_out_available": result["leave_one_out_available"],
        "misfit": {g: misfit[g] for g in result["groups"] if g in misfit},
        "active_obs": {g: active[g] for g in result["groups"] if g in active},
    }
    if args.overview_json:
        summary["cross_check"] = cross_check(Path(args.overview_json), result["per_param"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fields = ["parameter", "group", "ks", "above_floor", "top", "ks_all", "ks_all_minus_group", "loo_drop"]
    with (out / "influence.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({k: ("" if row[k] is None else row[k]) for k in fields})
    (out / "influence.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "influence.md").write_text(markdown(summary, result), encoding="utf-8")
    print(
        f"floor KS {floor:.3f} (n = {n}, alpha {args.alpha}); {summary['params_above_floor']} of "
        f"{summary['params']} parameters above it; {result['signature']['message']}"
    )
    return 1 if result["signature"]["fired"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--ahm-dir", required=True, help="folder with ks.csv (and prior.csv, misfit_obs_info.csv)"
    )
    parser.add_argument("--n", type=int, help="realizations in the prior (default: the rows of prior.csv)")
    parser.add_argument("--alpha", type=float, default=0.05, help="significance level of the floor")
    parser.add_argument("--share", type=float, default=0.4, help="top-group share that fires the signature")
    parser.add_argument(
        "--min-params", type=int, default=3, help="fewest parameters a dominant group must lead"
    )
    parser.add_argument("--overview-json", help="ahm_overview.py overview.json for a cross-check (optional)")
    parser.add_argument("--out", required=True, help="output folder")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
