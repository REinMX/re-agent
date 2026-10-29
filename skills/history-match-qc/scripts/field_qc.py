#!/usr/bin/env python3
"""Compare the prior and the posterior of a FIELD parameter, cell by cell. Read-only. Standard library only.

Input: one GRDECL file for each realization, with one keyword (for example PERMX). The script reads repeat counts
(3*0.25) and stops each keyword at '/'. Comments start with '--'. An ACTNUM GRDECL file selects the active cells.

For each active cell it computes the prior and posterior mean and sd over the realizations, and the prior min-max.
With --log it compares ln(value). All values of active cells must then be larger than 0.

Coded findings (each has the numbers, the threshold, and a limiter; they do not change the exit code):
    HMQC-FIELD-COLLAPSE   more than --collapse-share (0.5) of the active cells have posterior sd / prior sd
                          below --collapse-ratio (0.2).
    HMQC-FIELD-OUTSIDE    more than --outside-share (0.01) of the active cells have a posterior mean outside the
                          prior ensemble min-max.
With --truth (a synthetic truth field, for example of a benchmark) the script also reports, for the prior and the
posterior: the RMSE of the ensemble mean against the truth over the active cells (in ln with --log), the share of
active cells with the truth inside the ensemble P5-P95, and the share of active cells with
|truth - mean| / sd > --truth-z (3). P5 and P95 are linear percentiles: statistics.quantiles(method="inclusive"),
Hyndman-Fan type 7, the numpy default. A cell with sd 0 has |z| > --truth-z when the truth is not equal to the mean.
    HMQC-FIELD-TRUTH-WORSE     the posterior RMSE is larger than the prior RMSE by more than --truth-worse (0.02,
                               a share of the prior RMSE).
    HMQC-FIELD-TRUTH-COVERAGE  the posterior P5-P95 coverage is below --coverage-min (0.8) and at least
                               --coverage-drop (0.1) below the prior coverage.

Usage:
    field_qc.py --prior '<glob of prior GRDECL files>' --posterior '<glob of posterior GRDECL files>'
        [--keyword PERMX] [--actnum ACTNUM.grdecl] [--log] [--truth TRUTH.grdecl] [--outdir <new folder>] [--json]

Without --outdir the script prints markdown (or JSON with --json). With --outdir it writes field_qc.json and
field_qc.md in a new folder.
Exit code: 0 = read, 1 = a file cannot be used (bad text, wrong cell count, a value <= 0 with --log),
2 = usage error (no file matches a glob, fewer than 2 realizations, the output folder exists).
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import math
import re
import statistics
import sys
from pathlib import Path

REPEAT = re.compile(r"^(\d+)\*(.*)$")
TRUTH_LIMITS = {"truth_z": 3.0, "truth_worse": 0.02, "coverage_min": 0.8, "coverage_drop": 0.1}


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collapse_checks = _load_sibling("collapse_checks")


def parse_grdecl(path: Path) -> dict[str, list[float]]:
    """Each keyword of a GRDECL text file and its values. Raise ValueError for bad text."""
    keywords: dict[str, list[float]] = {}
    current: str | None = None
    values: list[float] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.split("--", 1)[0]
        for token in line.split():
            if current is None:
                if token == "/":
                    continue
                if not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,7}$", token):
                    raise ValueError(f"{path}:{number}: expected a keyword, got '{token}'")
                current, values = token.upper(), []
                continue
            closing = token.endswith("/")
            token = token[:-1] if closing else token
            if token:
                count, value = 1, token
                repeat = REPEAT.match(token)
                if repeat:
                    count, value = int(repeat.group(1)), repeat.group(2)
                    if count < 1 or not value:
                        raise ValueError(f"{path}:{number}: bad repeat '{token}'")
                try:
                    number_value = float(value.replace("D", "E").replace("d", "e"))
                except ValueError as error:
                    raise ValueError(f"{path}:{number}: '{token}' is not a number") from error
                if not math.isfinite(number_value):
                    raise ValueError(f"{path}:{number}: '{token}' is not finite")
                values.extend([number_value] * count)
            if closing:
                if current in keywords:
                    raise ValueError(f"{path}:{number}: keyword {current} is in the file twice")
                keywords[current] = values
                current = None
    if current is not None:
        raise ValueError(f"{path}: keyword {current} has no '/' terminator")
    if not keywords:
        raise ValueError(f"{path}: no keyword")
    return keywords


def read_field(path: Path, keyword: str | None) -> tuple[str, list[float]]:
    keywords = parse_grdecl(path)
    if keyword is None:
        if len(keywords) != 1:
            raise ValueError(f"{path}: {len(keywords)} keywords {sorted(keywords)}. Give --keyword")
        return next(iter(keywords.items()))
    if keyword.upper() not in keywords:
        raise ValueError(f"{path}: no keyword {keyword.upper()} (found {sorted(keywords)})")
    return keyword.upper(), keywords[keyword.upper()]


def read_ensemble(pattern: str, keyword: str | None) -> tuple[list[Path], str, list[list[float]]]:
    files = sorted(Path(p) for p in glob.glob(pattern) if Path(p).is_file())
    if not files:
        raise FileNotFoundError(f"no file matches {pattern}")
    if len(files) < 2:
        raise FileNotFoundError(f"{pattern} matches 1 file. The spread needs at least 2 realizations")
    names, members = set(), []
    for file in files:
        name, values = read_field(file, keyword)
        names.add(name)
        members.append(values)
    if len(names) > 1:
        raise ValueError(f"{pattern}: the files have different keywords {sorted(names)}. Give --keyword")
    sizes = {len(m) for m in members}
    if len(sizes) > 1:
        raise ValueError(f"{pattern}: the files have different cell counts {sorted(sizes)}")
    return files, names.pop(), members


def mean_sd(values: list[float]) -> tuple[float, float]:
    mean = math.fsum(values) / len(values)
    return mean, math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


def p5_p95(values: list[float]) -> tuple[float, float]:
    """Linear (Hyndman-Fan type 7) P5 and P95."""
    cuts = statistics.quantiles(values, n=20, method="inclusive")
    return cuts[0], cuts[-1]


def outside_z(truth: float, mean: float, sd: float, limit: float) -> bool:
    if sd > 0:
        return abs(truth - mean) / sd > limit
    return truth != mean


def truth_stats(
    members: list[list[float]], truth: list[float], active: list[bool], log: bool, truth_z: float
) -> dict:
    """RMSE of the ensemble mean, P5-P95 coverage, and share of |z| > truth_z over the active cells."""
    squared: list[float] = []
    covered = over_z = 0
    for cell, is_active in enumerate(active):
        if not is_active:
            continue
        values = [member[cell] for member in members]
        target = truth[cell]
        if log:
            values, target = [math.log(v) for v in values], math.log(target)
        mean, sd = mean_sd(values)
        low, high = p5_p95(values)
        squared.append((mean - target) ** 2)
        covered += low <= target <= high
        over_z += outside_z(target, mean, sd, truth_z)
    n_active = len(squared)
    return {
        "rmse": math.sqrt(math.fsum(squared) / n_active),
        "coverage_p5_p95": covered / n_active,
        "share_over_z": over_z / n_active,
        "cells_over_z": over_z,
    }


def truth_findings(
    prior: dict, posterior: dict, n_prior: int, n_post: int, scale: str, limits: dict
) -> list[dict]:
    limiter = (
        "Limiter: a synthetic truth only (the truth of a real field is not known); one truth field, so the result"
        " holds for this truth and not for the method; P5 and P95 from"
        f" N = {n_prior} prior and {n_post} posterior members are noisy"
    )
    found = []
    worse = limits["truth_worse"]
    if posterior["rmse"] > prior["rmse"] * (1 + worse):
        change = posterior["rmse"] / prior["rmse"] - 1 if prior["rmse"] > 0 else math.inf
        found.append(
            {
                "code": "HMQC-FIELD-TRUTH-WORSE",
                "finding": "field posterior mean moved away from the truth",
                "detail": (
                    f"RMSE of the ensemble mean against the truth: prior {prior['rmse']:.3f}, posterior"
                    f" {posterior['rmse']:.3f} ({change:+.1%}; threshold: more than {worse:+.0%}). Scale: {scale}."
                    f" {limiter}"
                ),
            }
        )
    minimum, drop = limits["coverage_min"], limits["coverage_drop"]
    before, after = prior["coverage_p5_p95"], posterior["coverage_p5_p95"]
    if after < minimum and before - after >= drop - 1e-9:
        found.append(
            {
                "code": "HMQC-FIELD-TRUTH-COVERAGE",
                "finding": "field posterior P5-P95 does not cover the truth",
                "detail": (
                    f"share of active cells with the truth inside P5-P95: prior {before:.2f}, posterior {after:.2f}"
                    f" (threshold: posterior below {minimum:g} and at least {drop:g} below the prior)."
                    f" Share with |z| > {limits['truth_z']:g}: prior {prior['share_over_z']:.1%}, posterior"
                    f" {posterior['share_over_z']:.1%}. Percentiles: linear (type 7). {limiter}"
                ),
            }
        )
    return found


def compare(
    prior: list[list[float]],
    posterior: list[list[float]],
    active: list[bool],
    log: bool,
    collapse_ratio: float,
    collapse_share: float,
    outside_share: float,
    truth: list[float] | None = None,
    truth_limits: dict | None = None,
) -> dict:
    cells = len(active)
    n_active = sum(active)
    if n_active == 0:
        raise ValueError("no active cell")
    if truth is not None and log:
        for cell, is_active in enumerate(active):
            if is_active and truth[cell] <= 0:
                raise ValueError(
                    f"truth cell {cell + 1}: a value is not larger than 0. --log needs positive values"
                )
    collapsed = outside = constant = 0
    ratios: list[float] = []
    for cell in range(cells):
        if not active[cell]:
            continue
        before = [member[cell] for member in prior]
        after = [member[cell] for member in posterior]
        if log:
            if min(before + after) <= 0:
                raise ValueError(
                    f"cell {cell + 1}: a value is not larger than 0. --log needs positive values"
                )
            before, after = [math.log(v) for v in before], [math.log(v) for v in after]
        _, prior_sd = mean_sd(before)
        post_mean, post_sd = mean_sd(after)
        if prior_sd > 0:
            ratio = post_sd / prior_sd
            ratios.append(ratio)
            if ratio < collapse_ratio:
                collapsed += 1
        else:
            constant += 1
        if not min(before) <= post_mean <= max(before):
            outside += 1
    n_prior, n_post = len(prior), len(posterior)
    floor = collapse_checks.std_ratio_noise_floor(min(n_prior, n_post))
    floor_text = f"{floor:.2f}" if floor is not None else "not available"
    scale = "ln" if log else "native"
    found = []
    with_spread = len(ratios)
    if with_spread and collapsed / with_spread > collapse_share:
        found.append(
            {
                "code": "HMQC-FIELD-COLLAPSE",
                "finding": "field spread collapsed",
                "detail": (
                    f"{collapsed} of {with_spread} active cells with prior spread ({collapsed / with_spread:.1%}) have posterior sd /"
                    f" prior sd < {collapse_ratio:g} (threshold: more than {collapse_share:.0%}). Scale: {scale}."
                    f" N = {n_prior} prior, {n_post} posterior; sampling-noise floor of the ratio {floor_text}."
                    " Limiter: cells near the observed wells can collapse correctly. A collapse far from the wells"
                    " points to missing localization or a too small ensemble"
                ),
            }
        )
    if outside / n_active > outside_share:
        found.append(
            {
                "code": "HMQC-FIELD-OUTSIDE",
                "finding": "field posterior mean outside the prior range",
                "detail": (
                    f"{outside} of {n_active} active cells ({outside / n_active:.1%}) have a posterior mean outside"
                    f" the prior ensemble min-max (threshold: more than {outside_share:.0%}). Scale: {scale}."
                    f" Limiter: with N = {n_prior} the prior min-max is narrower than the prior distribution,"
                    f" so a sample falls outside it with a chance of about 2/(N+1) = {2 / (n_prior + 1):.0%} per cell"
                ),
            }
        )
    truth_result = None
    if truth is not None:
        limits = truth_limits or TRUTH_LIMITS
        truth_result = {
            "prior": truth_stats(prior, truth, active, log, limits["truth_z"]),
            "posterior": truth_stats(posterior, truth, active, log, limits["truth_z"]),
            "percentiles": "linear, Hyndman-Fan type 7 (statistics.quantiles inclusive)",
            "thresholds": limits,
        }
        found += truth_findings(
            truth_result["prior"], truth_result["posterior"], n_prior, n_post, scale, limits
        )
    ordered = sorted(ratios)
    return {
        "cells": cells,
        "active_cells": n_active,
        "cells_without_prior_spread": constant,
        "scale": scale,
        "prior_realizations": n_prior,
        "posterior_realizations": n_post,
        "noise_floor": floor,
        "sd_ratio": {
            "median": statistics.median(ordered) if ordered else None,
            "min": ordered[0] if ordered else None,
            "max": ordered[-1] if ordered else None,
        },
        "collapsed_cells": collapsed,
        "outside_cells": outside,
        "thresholds": {
            "collapse_ratio": collapse_ratio,
            "collapse_share": collapse_share,
            "outside_share": outside_share,
        },
        "truth": truth_result,
        "findings": found,
    }


def markdown(result: dict) -> str:
    ratio = result["sd_ratio"]

    def fmt(value) -> str:
        return "-" if value is None else f"{value:.3g}"

    lines = [
        f"# Field QC: {result['keyword']}",
        "",
        f"Prior: {result['prior_realizations']} files. Posterior: {result['posterior_realizations']} files.",
        f"Cells: {result['cells']}. Active cells: {result['active_cells']}"
        f" ({'ACTNUM' if result['actnum'] else 'no ACTNUM, all cells'}). Scale: {result['scale']}.",
        "",
        "| Quantity | Value |",
        "|---|---:|",
        f"| median posterior/prior sd | {fmt(ratio['median'])} |",
        f"| min, max posterior/prior sd | {fmt(ratio['min'])}, {fmt(ratio['max'])} |",
        f"| cells with sd ratio < {result['thresholds']['collapse_ratio']:g} | {result['collapsed_cells']} |",
        f"| cells with posterior mean outside prior min-max | {result['outside_cells']} |",
        f"| cells without prior spread | {result['cells_without_prior_spread']} |",
        f"| sampling-noise floor of the ratio | {fmt(result['noise_floor'])} |",
    ]
    truth = result.get("truth")
    if truth is not None:
        before, after = truth["prior"], truth["posterior"]
        z = truth["thresholds"]["truth_z"]
        lines += [
            f"| RMSE of mean vs truth, prior / posterior | {before['rmse']:.3f} / {after['rmse']:.3f} |",
            f"| truth inside P5-P95, prior / posterior | {before['coverage_p5_p95']:.2f}"
            f" / {after['coverage_p5_p95']:.2f} |",
            f"| cells with abs(z) > {z:g}, prior / posterior | {before['share_over_z']:.1%}"
            f" / {after['share_over_z']:.1%} |",
        ]
    lines.append("")
    for item in result["findings"]:
        lines.append(f"- FINDING {item['code']} {item['finding']}: {item['detail']}")
    if not result["findings"]:
        lines.append("No coded finding.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prior", required=True, help="Quoted glob: one GRDECL file per prior realization")
    parser.add_argument(
        "--posterior", required=True, help="Quoted glob: one GRDECL file per posterior realization"
    )
    parser.add_argument(
        "--keyword", default=None, help="Keyword to read. Default: the only keyword in the file"
    )
    parser.add_argument("--actnum", type=Path, default=None, help="GRDECL file with ACTNUM")
    parser.add_argument("--log", action="store_true", help="Compare ln(value), for permeability")
    parser.add_argument("--collapse-ratio", type=float, default=0.2)
    parser.add_argument("--collapse-share", type=float, default=0.5)
    parser.add_argument("--outside-share", type=float, default=0.01)
    parser.add_argument("--truth", type=Path, default=None, help="GRDECL file with the synthetic truth field")
    for name, value in TRUTH_LIMITS.items():
        parser.add_argument(f"--{name.replace('_', '-')}", type=float, default=value)
    parser.add_argument(
        "--outdir", type=Path, default=None, help="New folder for field_qc.json and field_qc.md"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    for name in ("collapse_ratio", "collapse_share", "outside_share", "coverage_min", "coverage_drop"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 < value <= 1:
            print(f"ERROR: --{name.replace('_', '-')} must be in (0, 1]", file=sys.stderr)
            return 2
    for name in ("truth_z", "truth_worse"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0:
            print(f"ERROR: --{name.replace('_', '-')} must be finite and nonnegative", file=sys.stderr)
            return 2
    for path in (args.actnum, args.truth):
        if path is not None and not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2
    if args.outdir is not None and args.outdir.exists():
        print(f"ERROR: {args.outdir} exists. Give a new folder", file=sys.stderr)
        return 2
    try:
        prior_files, keyword, prior = read_ensemble(args.prior, args.keyword)
        post_files, _, posterior = read_ensemble(args.posterior, args.keyword or keyword)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        cells = len(prior[0])
        if len(posterior[0]) != cells:
            raise ValueError(f"prior has {cells} cells, posterior has {len(posterior[0])}")
        active = [True] * cells
        if args.actnum is not None:
            _, flags = read_field(args.actnum, "ACTNUM")
            if len(flags) != cells:
                raise ValueError(f"ACTNUM has {len(flags)} cells, the field has {cells}")
            active = [flag != 0 for flag in flags]
        truth = None
        if args.truth is not None:
            _, truth = read_field(args.truth, keyword)
            if len(truth) != cells:
                raise ValueError(f"the truth has {len(truth)} cells, the field has {cells}")
        limits = {name: getattr(args, name) for name in TRUTH_LIMITS}
        result = compare(
            prior,
            posterior,
            active,
            args.log,
            args.collapse_ratio,
            args.collapse_share,
            args.outside_share,
            truth,
            limits,
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    result = {
        "keyword": keyword,
        "prior_files": [str(p) for p in prior_files],
        "posterior_files": [str(p) for p in post_files],
        "actnum": str(args.actnum) if args.actnum else None,
        "truth_file": str(args.truth) if args.truth else None,
        **result,
    }
    text = markdown(result)
    if args.outdir is not None:
        args.outdir.mkdir(parents=True)
        (args.outdir / "field_qc.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (args.outdir / "field_qc.md").write_text(text, encoding="utf-8")
    print(json.dumps(result, indent=2) if args.json else text, end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
