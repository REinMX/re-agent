#!/usr/bin/env python3
"""Score a synthetic history match against its truth: response RMS ratios and parameter recovery. Read-only.

For a case with a known truth only (a synthetic or benchmark model). It computes the numbers that truth gates
such as G3 (history misfit), G4 (forecast against the truth) and G5 (parameter recovery) are built on.
It sets no threshold and gives no verdict. The caller applies the gate it registered before the run.

    truth_score.py --history <misfit_report outdir>/residuals.csv \\
        [--forecast <misfit_report outdir>/residuals.csv [--interval OBS]] \\
        [--dist FILE.dist ... --truth truth.txt --prior-runpaths GLOB --posterior-runpaths GLOB] \\
        [--prior-label prior] [--posterior-label posterior] [--json]

Responses. --history is misfit_report.py run with --prior and --posterior on the assimilated observations.
--forecast is the same run on an observation file of the truth on the forecast dates: VALUE is the truth
response and ERROR is the error the gate uses at that value. For each ensemble:
    mean_response_rms  sqrt(mean over observations of ((mean over members of SIMULATED) - OBSERVED) / ERROR)^2)
    member_mean_rms    mean over members of sqrt(mean over observations of ((SIMULATED - OBSERVED) / ERROR)^2)
and ratio = posterior mean_response_rms / prior mean_response_rms. The grid must be complete
(coverage.py from_residuals), and the prior and posterior must hold the same observations.
--interval OBS names one observation of the forecast file, for example the final FOPT. It is left out of the
forecast RMS. The report gives the posterior p10 and p90 of SIMULATED there (numerical percentiles, linear
interpolation, so p10 is the low value) and whether the truth lies between them.

Parameters. prior_qc.py reads the distribution files, the truth file and parameters.txt in each runpath.
The scale is log10 for LOGUNIF and LOGNORMAL, native otherwise. CONST and RAW parameters are skipped.
Per parameter: prior_sd and posterior_sd (sample sd, ddof 1), sd_reduction = 1 - posterior_sd / prior_sd,
bias_in_prior_sd = |posterior mean - truth| / prior_sd, and truth_in_posterior_p05_p95 (numerical).
The prior sd is the prior ensemble's, not the distribution's.

NumPy and pandas read the residual files; --help does not need them.
Exit code: 0 = done, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path

REQUIRED = ("ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED")
TEXT_COLUMNS = ("ENSEMBLE", "OBS", "KEY", "DATE", "STATUS")
NUMERIC_COLUMNS = ("REAL", "OBSERVED", "ERROR", "SIMULATED")
LIMITS = (
    "Synthetic cases only: the truth is known because the case was built from it.",
    "No threshold and no verdict. Apply the gate that was registered before the run.",
    "The forecast RMS is only as good as the error model written into the forecast observation file.",
    "Observations in one series are correlated, so an RMS over many dates is not a sum of independent terms.",
    "Percentiles are numerical (p10 is the low value), not the volumetric P10/P90 convention.",
)


def _module(filename):
    path = Path(__file__).resolve().with_name(filename)
    name = "_truthscore_" + path.stem + "_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]


def read_residuals(path: Path):
    """A misfit_report.py residuals.csv with numeric REAL, OBSERVED, ERROR and SIMULATED."""
    import pandas as pd

    header = pd.read_csv(path, nrows=0).columns
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ValueError(f"{path}: residuals.csv lacks the columns {missing}")
    table = pd.read_csv(
        path,
        keep_default_na=False,
        float_precision="round_trip",
        dtype={c: str for c in TEXT_COLUMNS if c in header},
    )
    for column in NUMERIC_COLUMNS:
        try:
            table[column] = pd.to_numeric(table[column], errors="raise")
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path}: {column} must be numeric") from error
    return table


def _grid(table, label: str, exclude: set[str]):
    """{OBS: (OBSERVED, ERROR, [SIMULATED by member])} of one complete ensemble grid."""
    _module("coverage.py").from_residuals(table, label)
    part = table[(table["ENSEMBLE"] == label) & ~table["OBS"].isin(exclude)]
    if part.empty:
        raise ValueError(f"ensemble {label!r} has no observation left to score")
    part = part.assign(REAL=part["REAL"].astype(int)).sort_values(["OBS", "REAL"])
    grid = {}
    for name, rows in part.groupby("OBS", sort=True):
        grid[name] = (
            float(rows["OBSERVED"].iloc[0]),
            float(rows["ERROR"].iloc[0]),
            [float(v) for v in rows["SIMULATED"]],
        )
    return grid


def _rms(grid: dict) -> dict:
    cells = list(grid.values())
    mean_resid = [(math.fsum(sim) / len(sim) - obs) / err for obs, err, sim in cells]
    members = [
        math.sqrt(math.fsum(((sim[i] - obs) / err) ** 2 for obs, err, sim in cells) / len(cells))
        for i in range(len(cells[0][2]))
    ]
    return {
        "mean_response_rms": math.sqrt(math.fsum(r * r for r in mean_resid) / len(cells)),
        "member_mean_rms": math.fsum(members) / len(members),
        "observations": len(cells),
        "members": len(members),
    }


def response_score(table, prior: str, posterior: str, exclude: set[str] | None = None) -> dict:
    """RMS of the prior and the posterior on the same observations, and their ratio."""
    exclude = set(exclude or ())
    grids = {label: _grid(table, label, exclude) for label in (prior, posterior)}
    if set(grids[prior]) != set(grids[posterior]):
        only = sorted(set(grids[prior]) ^ set(grids[posterior]))
        raise ValueError(f"the prior and posterior observation sets differ, for example {only[:3]}")
    result = {"prior": _rms(grids[prior]), "posterior": _rms(grids[posterior]), "excluded": sorted(exclude)}
    base = result["prior"]["mean_response_rms"]
    result["ratio"] = result["posterior"]["mean_response_rms"] / base if base > 0 else None
    return result


def _percentile(values: list[float], share: float) -> float:
    return _module("prior_qc.py").quantile(values, share)


def interval(table, label: str, obs: str) -> dict:
    """Posterior p10 and p90 of SIMULATED at one observation, and whether the truth (OBSERVED) is inside."""
    _module("coverage.py").from_residuals(table, label)
    rows = table[(table["ENSEMBLE"] == label) & (table["OBS"] == obs)]
    if rows.empty:
        raise ValueError(f"--interval {obs}: no such observation in ensemble {label!r}")
    values = [float(v) for v in rows["SIMULATED"]]
    truth = float(rows["OBSERVED"].iloc[0])
    low, high = _percentile(values, 0.10), _percentile(values, 0.90)
    return {
        "obs": obs,
        "key": str(rows["KEY"].iloc[0]),
        "date": str(rows["DATE"].iloc[0]),
        "truth": truth,
        "p10": low,
        "p90": high,
        "truth_inside": bool(low <= truth <= high),
        "members": len(values),
    }


def parameter_score(params: dict, prior: dict, posterior: dict, truth: dict) -> dict:
    """Spread reduction and truth bias of each uncertain parameter, in its sampling scale."""
    qc = _module("prior_qc.py")
    prior_values, _ = qc.match(prior, params)
    post_values, _ = qc.match(posterior, params)
    truth_values, _ = qc.match(truth, params)
    rows = {}
    for name, param in sorted(params.items()):
        if param["distribution"] in ("CONST", "RAW"):
            continue
        if name not in prior_values or name not in post_values:
            raise ValueError(f"{name}: no prior or posterior values")
        if name not in truth_values:
            raise ValueError(f"no truth value for {name}")
        base = [qc.to_scale(param, v) for v in prior_values[name]]
        post = [qc.to_scale(param, v) for v in post_values[name]]
        if len(base) < 2 or len(post) < 2:
            raise ValueError(f"{name}: fewer than two prior or posterior members")
        prior_sd, post_sd = statistics.stdev(base), statistics.stdev(post)
        if not prior_sd > 0:
            raise ValueError(f"{name}: the prior ensemble has no spread")
        true, post_mean = qc.to_scale(param, truth_values[name]), statistics.fmean(post)
        rows[name] = {
            "scale": qc.scale_name(param),
            "truth": true,
            "prior_mean": statistics.fmean(base),
            "prior_sd": prior_sd,
            "posterior_mean": post_mean,
            "posterior_sd": post_sd,
            "sd_reduction": 1.0 - post_sd / prior_sd,
            "bias_in_prior_sd": abs(post_mean - true) / prior_sd,
            "truth_in_posterior_p05_p95": bool(qc.quantile(post, 0.05) <= true <= qc.quantile(post, 0.95)),
            "members": [len(base), len(post)],
        }
    return rows


def _parameters(args) -> dict:
    qc = _module("prior_qc.py")
    params: dict = {}
    for path in args.dist:
        parsed, errors = qc.parse_dist(path)
        if errors:
            raise ValueError(f"{path}: {errors[0]['message']}")
        for param in parsed:
            if param["name"] in params:
                raise ValueError(f"duplicate parameter {param['name']}")
            params[param["name"]] = param
    return parameter_score(
        params,
        qc.read_posterior_runpaths(args.prior_runpaths),
        qc.read_posterior_runpaths(args.posterior_runpaths),
        qc.read_truth(args.truth),
    )


def score(args) -> dict:
    report: dict = {
        "tool": "truth_score.py",
        "labels": [args.prior_label, args.posterior_label],
        "limits": list(LIMITS),
    }
    report["history"] = response_score(read_residuals(args.history), args.prior_label, args.posterior_label)
    report["history"]["file"] = str(args.history)
    if args.forecast:
        table = read_residuals(args.forecast)
        excluded = {args.interval} if args.interval else set()
        report["forecast"] = response_score(table, args.prior_label, args.posterior_label, excluded)
        report["forecast"]["file"] = str(args.forecast)
        if args.interval:
            report["interval"] = interval(table, args.posterior_label, args.interval)
    if args.dist:
        report["parameters"] = _parameters(args)
    return report


def _print(report: dict) -> None:
    for part in ("history", "forecast"):
        if part in report:
            r = report[part]
            ratio = "n/a" if r["ratio"] is None else f"{r['ratio']:.4f}"
            print(
                f"{part}: mean-response RMS {r['prior']['mean_response_rms']:.4f} -> "
                f"{r['posterior']['mean_response_rms']:.4f}, ratio {ratio} "
                f"({r['posterior']['observations']} observations, {r['posterior']['members']} members)"
            )
    if "interval" in report:
        i = report["interval"]
        print(
            f"interval {i['obs']}: truth {i['truth']:g}, posterior p10 {i['p10']:g}, p90 {i['p90']:g}, "
            f"inside {i['truth_inside']}"
        )
    for name, row in report.get("parameters", {}).items():
        print(
            f"  {name:<20} {row['scale']:<6} sd reduction {row['sd_reduction']:.3f}  "
            f"bias {row['bias_in_prior_sd']:.3f} prior sd  truth in p05-p95 {row['truth_in_posterior_p05_p95']}"
        )
    print("No threshold and no verdict: apply the registered gate.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--history", required=True, type=Path, help="residuals.csv of the assimilated data")
    parser.add_argument("--forecast", type=Path, help="residuals.csv of a truth-forecast observation file")
    parser.add_argument("--interval", help="forecast observation for the p10-p90 check, left out of the RMS")
    parser.add_argument("--dist", type=Path, nargs="+", default=[], help="GEN_KW distribution files")
    parser.add_argument("--truth", type=Path, help="truth parameters, one 'NAME value' per line")
    parser.add_argument("--prior-runpaths", help="quoted glob of the prior runpaths (parameters.txt)")
    parser.add_argument("--posterior-runpaths", help="quoted glob of the posterior runpaths")
    parser.add_argument("--prior-label", default="prior")
    parser.add_argument("--posterior-label", default="posterior")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.interval and not args.forecast:
        parser.error("--interval needs --forecast")
    if args.dist and not (args.truth and args.prior_runpaths and args.posterior_runpaths):
        parser.error("--dist needs --truth, --prior-runpaths and --posterior-runpaths")
    try:
        report = score(args)
    except ImportError as error:
        print(f"ERROR: NumPy and pandas are required ({error})", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
