#!/usr/bin/env python3
"""Check the prior of a history match: GEN_KW distribution files. Read-only. Standard library only.

It parses GEN_KW distribution files (one parameter per line: NAME DISTRIBUTION arguments) and checks them.
With a truth or analog file it checks that the prior covers each value. With posterior values it checks
whether the posterior piles up at a bound, which is a sign that the prior is too narrow. It also compares the
posterior spread and mean with the prior (from the distribution, or from a prior ensemble when given).

Distributions and arguments (ERT GEN_KW):
    NORMAL mean std                 LOGNORMAL mean std (of log)      TRUNCATED_NORMAL mean std min max
    UNIFORM min max                 LOGUNIF min max                  DUNIF steps min max
    TRIANGULAR min mode max         ERRF min max skewness width      DERRF steps min max skewness width
    CONST value                     RAW
    The script reads the bounds. NORMAL has no bounds, and the script uses mean +- 3 std as the range.

Usage:
    prior_qc.py <file.dist> [<file2.dist> ...] [--truth <name value file>]
        [--posterior-csv <file.csv>] [--posterior-runpaths '<glob of runpaths with parameters.txt>']
        [--prior-csv <file.csv> | --prior-runpaths '<glob>'] [--collapse-ratio 0.2] [--collapse-share 0.5]
        [--truth-z 3] [--realizations N] [--min-realizations 20] [--edge-fraction 0.1] [--pile-fraction 0.3]
        [--log-pattern '(?<!REL)PERM|TRANS|MULT|^K[HVXYZ]?_'] [--not-log-pattern '<regex>'] [--json]

Truth file: one 'NAME value' per line. Comments start with '--' or '#'.
Posterior CSV: one column for each parameter, one row for each realization. Columns that are not numbers
(for example REAL or ENSEMBLE) are not used. A name can have a group prefix (GROUP:NAME). LOG10_ columns
are not used.

Findings (hints, they do not change the exit code):
    truth outside prior, truth near a bound, posterior at a bound, posterior outside the prior bounds,
    fewer realizations than parameters, fewer realizations than --min-realizations,
    a permeability-like parameter with a linear distribution (propose LOGUNIF or LOGNORMAL).
    A name is permeability-like when it matches --log-pattern and does not match --not-log-pattern.
    The default --not-log-pattern removes porosity, net-to-gross, and saturation names (PORO_MULT, MULT_NTG,
    SWCR_MULT, SATNUM_MULT). SW and SG count only as a full name part (SW_MULT, not TRANS_SWFAULT or MULTFLT_SW1).
Coded findings with a posterior (each has the numbers, the threshold, and a limiter):
    HMQC-COLLAPSE        more than --collapse-share of the parameters have posterior sd / prior sd below
                         --collapse-ratio. The scale is log10 for LOGUNIF and LOGNORMAL, native otherwise.
    HMQC-OUTSIDE-PRIOR   the posterior mean is outside the prior central 95 % interval.
    HMQC-TRUTH-Z         with --truth (synthetic cases only): parameters with |truth - posterior mean| /
                         posterior sd above --truth-z.
The prior sd and the interval come from the distribution as ERT samples it (a monotone transform of a standard
normal), or from the prior ensemble when --prior-csv or --prior-runpaths is given. Posterior parameters that the
prior ensemble does not cover (missing, or fewer than 2 values) are listed in a finding.
Errors (exit code 1): a line that cannot be read, an unknown distribution, wrong argument count,
bounds in the wrong order, a duplicate name, no truth or posterior name that matches a prior name,
a prior ensemble that gives no posterior parameter a usable prior (2 or more values).
Exit code: 0 = no errors, 1 = errors, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import glob
import importlib.util
import json
import math
import re
import statistics
import sys
from pathlib import Path

ARGUMENTS = {
    "NORMAL": ("mean", "std"),
    "LOGNORMAL": ("mean", "std"),
    "TRUNCATED_NORMAL": ("mean", "std", "min", "max"),
    "UNIFORM": ("min", "max"),
    "LOGUNIF": ("min", "max"),
    "DUNIF": ("steps", "min", "max"),
    "TRIANGULAR": ("min", "mode", "max"),
    "ERRF": ("min", "max", "skewness", "width"),
    "DERRF": ("steps", "min", "max", "skewness", "width"),
    "CONST": ("value",),
    "RAW": (),
}
LOG_DISTRIBUTIONS = {"LOGUNIF", "LOGNORMAL"}
DEFAULT_LOG_PATTERN = r"(?<!REL)PERM|TRANS|MULT|^K[HVXYZ]?_"
DEFAULT_NOT_LOG_PATTERN = r"PORO|NTG|SAT|S[WGO][WG]?CR|S[WG][LU](?![A-Z0-9])|(?<![A-Z0-9])S[WG](?![A-Z0-9])"
NORMAL = statistics.NormalDist()
QUADRATURE_POINTS = 4000


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collapse_checks = _load_sibling("collapse_checks")


def strip_comment(line: str) -> str:
    return re.split(r"--|#", line, maxsplit=1)[0].strip()


def bare(name: str) -> str:
    """NAME without a GROUP: prefix."""
    return name.split(":", 1)[-1]


def parse_dist(path: Path) -> tuple[list[dict], list[dict]]:
    """Return the parameters and the errors of one distribution file."""
    params: list[dict] = []
    errors: list[dict] = []

    def error(line: int, message: str) -> None:
        errors.append({"severity": "error", "file": str(path), "line": line, "message": message})

    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parts = strip_comment(raw).split()
        if not parts:
            continue
        if len(parts) < 2:
            error(number, f"'{raw.strip()}' needs a name and a distribution")
            continue
        name, dist, rest = parts[0], parts[1].upper(), parts[2:]
        if dist not in ARGUMENTS:
            error(number, f"{name}: unknown distribution {parts[1]}")
            continue
        expected = ARGUMENTS[dist]
        if len(rest) != len(expected):
            error(
                number,
                f"{name}: {dist} needs {len(expected)} arguments ({' '.join(expected)}), got {len(rest)}",
            )
            continue
        try:
            values = dict(zip(expected, (float(v) for v in rest), strict=True))
        except ValueError:
            error(number, f"{name}: an argument is not a number: {rest}")
            continue
        if not all(math.isfinite(v) for v in values.values()):
            error(number, f"{name}: an argument is not finite: {rest}")
            continue
        low, high = bounds(dist, values)
        if "std" in values and not values["std"] > 0:
            error(number, f"{name}: std must be larger than 0")
            continue
        if "width" in values and not values["width"] > 0:
            error(number, f"{name}: width must be larger than 0")
            continue
        if low is not None and high is not None and not low < high and dist != "CONST":
            error(number, f"{name}: min {low:g} must be smaller than max {high:g}")
            continue
        if dist == "LOGUNIF" and not values["min"] > 0:
            error(number, f"{name}: LOGUNIF needs min > 0")
            continue
        if dist == "TRIANGULAR" and not values["min"] <= values["mode"] <= values["max"]:
            error(number, f"{name}: TRIANGULAR mode {values['mode']:g} is outside min-max")
            continue
        if dist in ("DUNIF", "DERRF") and (values["steps"] < 2 or values["steps"] != int(values["steps"])):
            error(number, f"{name}: steps must be an integer >= 2")
            continue
        params.append(
            {
                "name": name,
                "distribution": dist,
                "arguments": values,
                "min": low,
                "max": high,
                "file": str(path),
                "line": number,
            }
        )
    return params, errors


def bounds(dist: str, values: dict) -> tuple[float | None, float | None]:
    """Hard bounds, or mean +- 3 std for NORMAL. LOGNORMAL has a lower bound of 0 only."""
    if dist == "CONST":
        return values["value"], values["value"]
    if dist == "NORMAL":
        return values["mean"] - 3 * values["std"], values["mean"] + 3 * values["std"]
    if dist == "LOGNORMAL":
        return 0.0, None
    if dist == "RAW":
        return None, None
    return values["min"], values["max"]


def position(param: dict, value: float) -> float | None:
    """Position of value in the prior range: 0 at min, 1 at max. Log scale for LOGUNIF."""
    low, high = param["min"], param["max"]
    if low is None or high is None or not high > low:
        return None
    if param["distribution"] == "LOGUNIF":
        if value <= 0:
            return -math.inf
        return (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))
    return (value - low) / (high - low)


def transform(param: dict, z: float) -> float:
    """The value that ERT gives for the standard normal sample z. Same as ert/config/distribution.py."""
    dist, a = param["distribution"], param["arguments"]
    if dist == "NORMAL":
        return a["mean"] + z * a["std"]
    if dist == "LOGNORMAL":
        return math.exp(a["mean"] + z * a["std"])
    if dist == "TRUNCATED_NORMAL":
        return min(max(a["mean"] + z * a["std"], a["min"]), a["max"])
    if dist in ("ERRF", "DERRF"):
        y = NORMAL.cdf((z + a["skewness"]) / a["width"])
        if dist == "DERRF":
            steps = int(a["steps"])
            y = max(math.ceil(y * steps) - 1, 0) / (steps - 1)
        return a["min"] + y * (a["max"] - a["min"])
    y = NORMAL.cdf(z)
    if dist == "UNIFORM":
        return a["min"] + y * (a["max"] - a["min"])
    if dist == "LOGUNIF":
        return math.exp(math.log(a["min"]) + y * (math.log(a["max"]) - math.log(a["min"])))
    if dist == "DUNIF":
        steps = int(a["steps"])
        return min(math.floor(y * steps), steps - 1) / (steps - 1) * (a["max"] - a["min"]) + a["min"]
    if dist == "TRIANGULAR":
        low, mode, high = a["min"], a["mode"], a["max"]
        if y < (mode - low) / (high - low):
            return low + math.sqrt(y * (high - low) * (mode - low))
        return high - math.sqrt((1 - y) * (high - low) * (high - mode))
    raise ValueError(f"no transform for {dist}")


def scale_name(param: dict) -> str:
    return "log10" if param["distribution"] in LOG_DISTRIBUTIONS else "native"


def to_scale(param: dict, value: float) -> float:
    """The value in the comparison scale: log10 for LOGUNIF and LOGNORMAL, native otherwise."""
    if param["distribution"] not in LOG_DISTRIBUTIONS:
        return value
    if not value > 0:
        raise ValueError(f"{param['name']}: {param['distribution']} value {value:g} is not positive")
    return math.log10(value)


def prior_spread_from_distribution(param: dict) -> tuple[float, float, float]:
    """(sd in the comparison scale, 2.5 % value, 97.5 % value) of the prior as ERT samples it."""
    a = param["arguments"]
    low, high = transform(param, NORMAL.inv_cdf(0.025)), transform(param, NORMAL.inv_cdf(0.975))
    if param["distribution"] == "NORMAL":
        return a["std"], low, high
    if param["distribution"] == "LOGNORMAL":
        return a["std"] / math.log(10), low, high
    values = [
        to_scale(param, transform(param, NORMAL.inv_cdf((i + 0.5) / QUADRATURE_POINTS)))
        for i in range(QUADRATURE_POINTS)
    ]
    return statistics.pstdev(values), low, high


def quantile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    where = share * (len(ordered) - 1)
    below = math.floor(where)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (where - below) * (ordered[above] - ordered[below])


def spread_table(
    params: dict[str, dict],
    posterior: dict[str, list[float]],
    prior: dict[str, list[float]] | None,
    truth: dict[str, float] | None,
) -> list[dict]:
    """Prior and posterior sd, posterior mean, and the prior 95 % interval of each matched uncertain parameter."""
    post, _ = match(posterior, params)
    prior_values = match(prior, params)[0] if prior is not None else {}
    truth_values = match(truth, params)[0] if truth is not None else {}
    rows = []
    for name, values in post.items():
        p = params[name]
        if p["distribution"] in ("CONST", "RAW") or len(values) < 2:
            continue
        scaled = [to_scale(p, v) for v in values]
        if name in prior_values:
            base = [to_scale(p, v) for v in prior_values[name]]
            if len(base) < 2:
                continue
            prior_sd = statistics.stdev(base)
            low, high = quantile(prior_values[name], 0.025), quantile(prior_values[name], 0.975)
            source = "prior ensemble"
        elif prior is not None:
            continue
        else:
            prior_sd, low, high = prior_spread_from_distribution(p)
            source = "distribution"
        post_sd = statistics.stdev(scaled)
        mean_scaled = statistics.fmean(scaled)
        mean = 10**mean_scaled if scale_name(p) == "log10" else mean_scaled
        row = {
            "parameter": name,
            "scale": scale_name(p),
            "prior_source": source,
            "prior_sd": prior_sd,
            "posterior_sd": post_sd,
            "sd_ratio": post_sd / prior_sd if prior_sd > 0 else None,
            "posterior_mean": mean,
            "prior_p2_5": low,
            "prior_p97_5": high,
            "truth": None,
            "truth_z": None,
        }
        if name in truth_values:
            value = truth_values[name]
            row["truth"] = value
            if scale_name(p) == "log10" and not value > 0:
                row["truth_z"] = math.inf
            else:
                gap = abs(to_scale(p, value) - mean_scaled)
                row["truth_z"] = gap / post_sd if post_sd > 0 else (math.inf if gap > 0 else 0.0)
        rows.append(row)
    return rows


def prior_coverage(
    params: dict[str, dict], posterior: dict[str, list[float]], prior: dict[str, list[float]]
) -> dict[str, list[str]]:
    """Posterior parameters that the prior ensemble does not cover, and prior names not in the distribution files."""
    post = match(posterior, params)[0]
    prior_values, unknown = match(prior, params)
    checked = [
        name
        for name, values in post.items()
        if params[name]["distribution"] not in ("CONST", "RAW") and len(values) >= 2
    ]
    return {
        "covered": [name for name in checked if len(prior_values.get(name, [])) >= 2],
        "missing": [name for name in checked if name not in prior_values],
        "too_few": [name for name in checked if name in prior_values and len(prior_values[name]) < 2],
        "unknown": unknown,
    }


def spread_findings(rows: list[dict], realizations: int, n_params: int, args) -> list[dict]:
    """HMQC-COLLAPSE, HMQC-OUTSIDE-PRIOR, and HMQC-TRUTH-Z findings from the spread table."""
    found = []
    ratios = [r for r in rows if r["sd_ratio"] is not None]
    collapsed = [r for r in ratios if r["sd_ratio"] < args.collapse_ratio]
    if ratios and len(collapsed) / len(ratios) > args.collapse_share:
        floor = collapse_checks.std_ratio_noise_floor(realizations)
        floor_text = f"{floor:.2f}" if floor is not None else "not available (N < 2)"
        scales = sorted({r["scale"] for r in collapsed})
        found.append(
            finding(
                "posterior spread collapsed",
                None,
                f"{len(collapsed)} of {len(ratios)} parameters ({len(collapsed) / len(ratios):.0%}) have posterior"
                f" sd / prior sd < {args.collapse_ratio:g} (threshold: more than {args.collapse_share:.0%})."
                f" N = {realizations}, {n_params} uncertain parameters, sampling-noise floor of the ratio"
                f" {floor_text}. Scale: {' and '.join(scales)} (log10 for LOGUNIF and LOGNORMAL)."
                " Limiter: a parameter that the data resolve can collapse correctly, and with N <= the number of"
                " parameters the smoother reduces the spread by construction. Check the misfit and the"
                " observation errors before you trust the posterior spread",
                code="HMQC-COLLAPSE",
            )
        )
    for r in rows:
        if r["prior_p2_5"] <= r["posterior_mean"] <= r["prior_p97_5"]:
            continue
        found.append(
            finding(
                "posterior mean outside the prior 95 % interval",
                r["parameter"],
                f"posterior mean {r['posterior_mean']:.4g} is outside the prior central 95 % interval"
                f" {r['prior_p2_5']:.4g}-{r['prior_p97_5']:.4g} (from the {r['prior_source']})."
                " Limiter: strong data can move a parameter there, and the mean of a small ensemble is noisy."
                " Check the prior and the observation errors before you accept it",
                code="HMQC-OUTSIDE-PRIOR",
            )
        )
    scored = [r for r in rows if r["truth_z"] is not None]
    far = sorted((r for r in scored if r["truth_z"] > args.truth_z), key=lambda r: -r["truth_z"])
    if far:
        found.append(
            finding(
                "truth far from the posterior",
                None,
                f"{len(far)} of {len(scored)} parameters have |truth - posterior mean| / posterior sd >"
                f" {args.truth_z:g}: "
                + ", ".join(f"{r['parameter']} z={r['truth_z']:.3g}" for r in far[:10])
                + ". The posterior is overconfident or biased. Limiter: synthetic cases only; with a small N the"
                " posterior sd is uncertain, and a collapsed sd makes z large",
                code="HMQC-TRUTH-Z",
            )
        )
    return found


def read_truth(path: Path) -> dict[str, float]:
    truth: dict[str, float] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parts = strip_comment(raw).split()
        if not parts:
            continue
        if len(parts) != 2:
            raise ValueError(f"{path}:{number}: expected 'NAME value'")
        try:
            value = float(parts[1])
        except ValueError as error:
            raise ValueError(f"{path}:{number}: '{parts[1]}' is not a number") from error
        if not math.isfinite(value):
            raise ValueError(f"{path}:{number}: value must be finite")
        if parts[0] in truth:
            raise ValueError(f"{path}:{number}: duplicate parameter {parts[0]}")
        truth[parts[0]] = value
    return truth


def read_posterior_csv(path: Path) -> dict[str, list[float]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path}: no rows")
    columns: dict[str, list[float]] = {}
    for name in rows[0]:
        if name is None:
            raise ValueError(f"{path}: row has more fields than the header")
        if name.upper() in {"REAL", "REALIZATION", "IENS", "ITER"} or name.startswith("LOG10_"):
            continue
        values = []
        invalid = False
        for row in rows:
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                invalid = True
                continue
            if not math.isfinite(value):
                raise ValueError(f"{path}: {name} contains a non-finite value")
            values.append(value)
        if invalid and values:
            raise ValueError(f"{path}: {name} is missing a numeric value in a realization")
        if not values:
            continue
        columns[name] = values
    return columns


def read_posterior_runpaths(pattern: str) -> dict[str, list[float]]:
    """parameters.txt of each runpath: one 'NAME value' per line."""
    folders = sorted(p for p in glob.glob(pattern) if Path(p).is_dir())
    if not folders:
        raise ValueError(f"no runpath matches {pattern}")
    columns: dict[str, list[float]] = {}
    expected = None
    for folder in folders:
        file = Path(folder) / "parameters.txt"
        if not file.is_file():
            raise ValueError(f"{folder}: no parameters.txt")
        member = {name: value for name, value in read_truth(file).items() if not name.startswith("LOG10_")}
        if expected is not None and set(member) != expected:
            raise ValueError("the runpaths have different parameter sets")
        expected = set(member)
        for name, value in member.items():
            if not name.startswith("LOG10_"):
                columns.setdefault(name, []).append(value)
    counts = {len(v) for v in columns.values()}
    if len(counts) > 1:
        raise ValueError("the runpaths have different parameter sets")
    return columns


def match(
    names: dict[str, float] | dict[str, list[float]], params: dict[str, dict]
) -> tuple[dict, list[str]]:
    """Match names with or without a GROUP: prefix to prior names. Return matched items and unknown names."""
    by_bare = {bare(n): n for n in params}
    matched, unknown = {}, []
    for name, value in names.items():
        key = name if name in params else by_bare.get(bare(name))
        if key is None:
            unknown.append(name)
        else:
            matched[key] = value
    return matched, unknown


def finding(kind: str, name: str | None, detail: str, code: str | None = None) -> dict:
    item = {"finding": kind, "parameter": name, "detail": detail}
    if code is not None:
        item["code"] = code
    return item


def problem(message: str) -> dict:
    return {"severity": "error", "file": "", "line": 0, "message": message}


def check(
    params: dict[str, dict],
    truth: dict[str, float] | None,
    posterior: dict[str, list[float]] | None,
    realizations: int | None,
    args,
) -> tuple[list[dict], list[dict]]:
    """Return (findings, errors)."""
    found: list[dict] = []
    errors: list[dict] = []
    log_re = re.compile(args.log_pattern, re.I)
    not_log_re = re.compile(args.not_log_pattern, re.I)
    linear = ("UNIFORM", "DUNIF", "TRIANGULAR", "TRUNCATED_NORMAL")
    for name, p in params.items():
        if log_re.search(bare(name)) and not not_log_re.search(bare(name)) and p["distribution"] in linear:
            span = p["max"] / p["min"] if p["min"] and p["min"] > 0 else None
            span_text = f", max/min = {span:.3g}" if span else ""
            found.append(
                finding(
                    "linear distribution for a permeability-like parameter",
                    name,
                    f"{p['distribution']} {p['min']:g}-{p['max']:g}{span_text}. Propose LOGUNIF (or LOGNORMAL)."
                    " The effect is large when max/min is 10 or more",
                )
            )
    if truth is not None:
        matched, unknown = match(truth, params)
        if not matched:
            errors.append(problem("no truth name matches a prior name"))
        if unknown:
            found.append(
                finding("truth name not in the prior", None, f"{len(unknown)} names: {unknown[:10]}")
            )
        for name, value in matched.items():
            p = params[name]
            if p["distribution"] == "LOGNORMAL":
                if value <= 0:
                    found.append(
                        finding(
                            "truth outside prior",
                            name,
                            f"truth {value:g}, LOGNORMAL requires a positive value",
                        )
                    )
                continue
            where = position(p, value)
            if p["distribution"] == "CONST":
                if value != p["min"]:
                    found.append(
                        finding("truth outside prior", name, f"truth {value:g}, prior CONST {p['min']:g}")
                    )
                continue
            if where is None:
                continue
            label = "mean +- 3 std" if p["distribution"] == "NORMAL" else "range"
            if where < 0 or where > 1:
                found.append(
                    finding(
                        "truth outside prior",
                        name,
                        f"truth {value:g}, prior {p['distribution']} {label} {p['min']:g}-{p['max']:g}."
                        + (
                            " This is a coverage heuristic, not a hard bound. Check the prior spread or the truth"
                            if p["distribution"] == "NORMAL"
                            else " The update cannot reach it. Widen the prior or check the truth"
                        ),
                    )
                )
            elif min(where, 1 - where) <= args.edge_fraction:
                side = "lower" if where < 0.5 else "upper"
                found.append(
                    finding(
                        "truth near a bound",
                        name,
                        f"truth {value:g} is {where:.0%} of the {label} {p['min']:g}-{p['max']:g}"
                        f" (near the {side} bound). The posterior can pile up there",
                    )
                )
    if posterior is not None:
        matched, unknown = match(posterior, params)
        if not matched:
            errors.append(problem("no posterior column matches a prior name"))
        if unknown:
            found.append(
                finding("posterior column not in the prior", None, f"{len(unknown)} names: {unknown[:10]}")
            )
        for name, values in matched.items():
            p = params[name]
            if p["distribution"] in ("CONST", "RAW", "NORMAL", "LOGNORMAL"):
                continue
            places = [position(p, v) for v in values]
            outside = [
                v
                for v, w in zip(values, places, strict=True)
                if w is not None and (w < -1e-9 or w > 1 + 1e-9)
            ]
            if outside:
                found.append(
                    finding(
                        "posterior outside the prior bounds",
                        name,
                        f"{len(outside)} of {len(values)} values outside {p['min']:g}-{p['max']:g}."
                        " Check that the values are in the parameter scale",
                    )
                )
                continue
            for side, test in (
                ("lower", lambda w: w <= args.edge_fraction),
                ("upper", lambda w: w >= 1 - args.edge_fraction),
            ):
                near = sum(1 for w in places if w is not None and test(w))
                if values and near / len(values) >= args.pile_fraction:
                    found.append(
                        finding(
                            "posterior at a bound",
                            name,
                            f"{near} of {len(values)} realizations are within {args.edge_fraction:.0%} of the"
                            f" {side} bound ({p['min' if side == 'lower' else 'max']:g}). The prior may be too narrow",
                        )
                    )
    free = [n for n, p in params.items() if p["distribution"] not in ("CONST", "RAW")]
    if realizations is not None:
        if realizations <= len(free):
            found.append(
                finding(
                    "fewer realizations than parameters",
                    None,
                    f"N = {realizations} for {len(free)} uncertain parameters. The ensemble is rank deficient."
                    " Expect spurious correlations and too much spread reduction",
                )
            )
        if realizations < args.min_realizations:
            found.append(
                finding(
                    "small ensemble",
                    None,
                    f"N = {realizations} is below {args.min_realizations}. Statistics from this ensemble are weak",
                )
            )
    return found, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path, help="GEN_KW distribution files")
    parser.add_argument("--truth", type=Path, default=None, help="Truth or analog values: NAME value")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--posterior-csv", type=Path, default=None)
    source.add_argument(
        "--posterior-runpaths", default=None, help="Quoted glob of runpaths with parameters.txt"
    )
    prior_source = parser.add_mutually_exclusive_group()
    prior_source.add_argument(
        "--prior-csv", type=Path, default=None, help="Prior ensemble. Default: the distribution"
    )
    prior_source.add_argument("--prior-runpaths", default=None, help="Quoted glob of prior runpaths")
    parser.add_argument(
        "--collapse-ratio", type=float, default=0.2, help="Collapsed: posterior sd / prior sd below this"
    )
    parser.add_argument(
        "--collapse-share", type=float, default=0.5, help="HMQC-COLLAPSE: more than this share collapsed"
    )
    parser.add_argument("--truth-z", type=float, default=3.0, help="HMQC-TRUTH-Z: z above this value")
    parser.add_argument(
        "--realizations", type=int, default=None, help="Ensemble size N. Default: posterior rows"
    )
    parser.add_argument("--min-realizations", type=int, default=20)
    parser.add_argument(
        "--edge-fraction", type=float, default=0.1, help="Near a bound: within this part of the range"
    )
    parser.add_argument(
        "--pile-fraction", type=float, default=0.3, help="Posterior at a bound: at least this share"
    )
    parser.add_argument(
        "--log-pattern", default=DEFAULT_LOG_PATTERN, help="Regex for permeability-like names"
    )
    parser.add_argument(
        "--not-log-pattern",
        default=DEFAULT_NOT_LOG_PATTERN,
        help="Regex for names that are not permeability-like (porosity, NTG, saturation)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    for name in ("edge_fraction", "pile_fraction", "collapse_ratio", "collapse_share"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 < value <= 1:
            print(f"ERROR: --{name.replace('_', '-')} must be in (0, 1]", file=sys.stderr)
            return 2
    if not math.isfinite(args.truth_z) or args.truth_z <= 0:
        print("ERROR: --truth-z must be positive", file=sys.stderr)
        return 2
    if (args.prior_csv or args.prior_runpaths) and not (args.posterior_csv or args.posterior_runpaths):
        print("ERROR: a prior ensemble needs --posterior-csv or --posterior-runpaths", file=sys.stderr)
        return 2
    if args.min_realizations < 1 or (args.realizations is not None and args.realizations < 1):
        print("ERROR: realization counts must be positive", file=sys.stderr)
        return 2
    for option, pattern in (("--log-pattern", args.log_pattern), ("--not-log-pattern", args.not_log_pattern)):
        try:
            re.compile(pattern)
        except re.error as error:
            print(f"ERROR: {option}: {error}", file=sys.stderr)
            return 2
    for path in [
        *args.files,
        *(p for p in (args.truth, args.posterior_csv, args.prior_csv) if p is not None),
    ]:
        if not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2

    params: dict[str, dict] = {}
    problems: list[dict] = []
    for path in args.files:
        found, errors = parse_dist(path)
        problems += errors
        for p in found:
            if p["name"] in params:
                first = params[p["name"]]
                problems.append(
                    {
                        "severity": "error",
                        "file": p["file"],
                        "line": p["line"],
                        "message": f"{p['name']} is also defined in {Path(first['file']).name}:{first['line']}",
                    }
                )
            else:
                params[p["name"]] = p
    if not params and not problems:
        print("ERROR: no parameter found", file=sys.stderr)
        return 2
    try:
        truth = read_truth(args.truth) if args.truth else None
        posterior = None
        if args.posterior_csv:
            posterior = read_posterior_csv(args.posterior_csv)
        elif args.posterior_runpaths:
            posterior = read_posterior_runpaths(args.posterior_runpaths)
        prior = None
        if args.prior_csv:
            prior = read_posterior_csv(args.prior_csv)
        elif args.prior_runpaths:
            prior = read_posterior_runpaths(args.prior_runpaths)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    realizations = args.realizations
    if realizations is None and posterior:
        realizations = len(next(iter(posterior.values())))
    found, errors = check(params, truth, posterior, realizations, args)
    problems += errors
    spread: list[dict] = []
    if posterior:
        try:
            spread = spread_table(params, posterior, prior, truth)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        if prior is not None:
            cover = prior_coverage(params, posterior, prior)
            source = args.prior_csv or args.prior_runpaths
            gaps = (
                f"missing in the prior: {cover['missing'][:10]}; fewer than 2 prior values: {cover['too_few'][:10]};"
                f" prior names not in the distribution files: {cover['unknown'][:10]}"
            )
            if not cover["covered"] and (cover["missing"] or cover["too_few"]):
                problems.append(
                    problem(
                        f"no posterior parameter has a usable prior (2 or more values) in {source}: {gaps}."
                        " The spread checks need the prior ensemble. Check the names, or omit the prior option to"
                        " use the distribution"
                    )
                )
            elif cover["missing"] or cover["too_few"] or cover["unknown"]:
                found.append(
                    finding(
                        "prior ensemble does not cover the posterior",
                        None,
                        f"{len(cover['covered'])} of {len(cover['covered']) + len(cover['missing']) + len(cover['too_few'])}"
                        f" posterior parameters have a usable prior in {source}; {gaps}. The spread checks skip the"
                        " parameters without a usable prior",
                    )
                )
        sizes = [len(next(iter(posterior.values())))]
        if prior:
            sizes.append(len(next(iter(prior.values()))))
        uncertain = sum(1 for p in params.values() if p["distribution"] not in ("CONST", "RAW"))
        found += spread_findings(spread, min(sizes), uncertain, args)

    result = {
        "parameters": list(params.values()),
        "uncertain_parameters": sum(1 for p in params.values() if p["distribution"] not in ("CONST", "RAW")),
        "realizations": realizations,
        "spread": spread,
        "findings": found,
        "problems": problems,
        "thresholds": {
            "collapse_ratio": args.collapse_ratio,
            "collapse_share": args.collapse_share,
            "truth_z": args.truth_z,
            "min_realizations": args.min_realizations,
            "edge_fraction": args.edge_fraction,
            "pile_fraction": args.pile_fraction,
            "log_pattern": args.log_pattern,
            "not_log_pattern": args.not_log_pattern,
        },
    }
    if args.json:
        for row in spread:
            if row["truth_z"] == math.inf:
                row["truth_z"] = "inf"
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Parameters: {len(params)} ({result['uncertain_parameters']} uncertain) in {len(args.files)} file(s)."
            f" Realizations: {realizations if realizations is not None else 'not given'}"
        )
        for p in params.values():
            args_text = " ".join(f"{v:g}" for v in p["arguments"].values())
            print(f"  {p['name']:<28} {p['distribution']:<16} {args_text}")
        if spread:
            print("  parameter                    scale   prior sd  post sd  ratio   post mean  prior 95 %")
            for r in spread:
                ratio = "-" if r["sd_ratio"] is None else f"{r['sd_ratio']:.3f}"
                print(
                    f"  {r['parameter']:<28} {r['scale']:<7} {r['prior_sd']:<9.3g} {r['posterior_sd']:<8.3g}"
                    f" {ratio:<7} {r['posterior_mean']:<10.4g} {r['prior_p2_5']:.4g}-{r['prior_p97_5']:.4g}"
                )
        for item in found:
            code = f"{item['code']} " if "code" in item else ""
            print(f"FINDING {code}{item['finding']}: {item['parameter'] or ''} {item['detail']}")
        for p in problems:
            where = f"{Path(p['file']).name}:{p['line']}" if p["file"] else ""
            print(f"error    {where}: {p['message']}")
        print(f"{len(problems)} errors, {len(found)} findings")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
