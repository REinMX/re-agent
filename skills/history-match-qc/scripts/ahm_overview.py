#!/usr/bin/env python3
"""One-shot overview of an ES or ES-MDA history match. Read-only. Deterministic.

It builds on the outputs that exist and does not compute them again:
    misfit_report.py output (--misfit-dir): residuals.csv and manifest.json
    prior_qc.py: distribution files, scales, bounds, and its spread findings (run in-process)
    collapse_checks.py: chi-square band, std ratio noise floor, trust gate
    field_qc.py JSON (--field-qc): field spread and truth statistics
It adds: static vs dynamic parameter summary, prior-predictive coverage, observation groups, a linear-Gaussian
estimate of which data drive the update, conflicts between data groups, noise checks, forecast change with a
statistical attribution, and a run-based attribution from base/static/dynamic/all cases.

Usage:
    ahm_overview.py --misfit-dir DIR --dist FILE [--dist FILE ...]
        (--prior-runpaths GLOB --posterior-runpaths GLOB | --prior-params-csv F --posterior-params-csv F)
        [--prior-label prior] [--posterior-label posterior] [--truth FILE] [--truth-misfit-dir DIR]
        [--field-qc JSON ...] [--forecast-prior CSV --forecast-posterior CSV] [--forecast-truth KEY[@AS_OF]=VALUE ...]
        [--attribution-csv CSV] [--param-class REGEX=static:CATEGORY ...] [thresholds] --outdir DIR
    ahm_overview.py ... --write-attribution DIR
        writes base/static/dynamic/all parameters.txt for the run-based attribution and exits.

Parameter CSV: one row per realization, a REAL column, one column per parameter (GROUP:NAME or NAME).
Forecast CSV: the ensemble-at-date per_real.csv (REAL, AS_OF, KEY, VALUE, ...).
Attribution CSV: CASE, KEY, VALUE with CASE in base, static, dynamic, all.

Outputs in --outdir: overview.json, overview.md, parameters.csv, observation_groups.csv,
observations_influence.csv, conflicts.csv.
Exit code: 0 = written (fired checks do not change it), 1 = unreadable or inconsistent input, 2 = bad options.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import numpy as np
import pandas as pd
import runpath_lib

HERE = Path(__file__).resolve().parent
SCHEMA = "ahm-overview/1"
REFERENCE = "references/drogon-official.md"
P10_P90 = "P10/P90 numerical: P10 = 10th percentile (low value), P90 = 90th percentile (high value)"
FIXED = ("CONST", "RAW")
HARD_BOUND_FREE = ("NORMAL", "LOGNORMAL", "RAW", "CONST")
EDGE_FRACTION = 0.1
PILE_FRACTION = 0.3
TOP = 20
EXAMPLES = 10
MD_CHECKS = 10
CASES = ("base", "static", "dynamic", "all")
LEVELS = ("family", "key", "well")
MIN_CONFLICT_PARAMS = 3

DEFAULT_CLASSES = (
    ("static", "kvkh", r"KVKH|KV_?KH|PERMZ"),
    ("static", "perm", r"(?<!REL)PERM|^K[XYH]_|^PERMX|^PERMY"),
    ("static", "poro", r"PORO"),
    ("static", "ntg", r"NTG"),
    ("static", "facies", r"APS|FACIES|ISOTREND|VOLFRAC|GRF"),
    ("dynamic", "fault", r"MULTFLT|FAULT|FLT"),
    ("dynamic", "relperm", r"(^|_)KR|RELPERM|SWOF|SGOF|SWL|SWCR|SOWCR|SORW|SGCR|COREY|^LET_"),
    ("dynamic", "pvt", r"PVT|^BO_|^RS_|VISC|DENS|BUBBLE"),
    ("dynamic", "contacts", r"OWC|GOC|GWC|FWL|CONTACT"),
    ("dynamic", "multiplier", r"MULTZ|MULTREGT|MULT|TRANS"),
    ("dynamic", "aquifer", r"^AQ"),
)

G = "ERT gentle introduction"
RATIONALE = {
    "HMQC-PRIOR-PREDICTIVE": f"{G}, AHM: prior responses should span the observations (good coverage)",
    "HMQC-OBS-OUTLIER": f"{G}, innovation: a large innovation gives a large update; check the data or the error",
    "HMQC-OBS-DOMINANT": f"{G}, response covariance: correlated points are counted as independent data",
    "HMQC-OBS-CONFLICT": f"{G}, ES equation: group contributions K_g e_g that point in opposite directions",
    "HMQC-PARAM-TUG": f"{G}, ES equation: one parameter pulled both ways by different data groups",
    "HMQC-SPURIOUS-UPDATE": f"{G}, spurious correlations; adaptive localization threshold 3/sqrt(N); ERT Drogon localization page",
    "HMQC-INFLUENCE-RANK": f"{G}, cross-covariance: noisy when N is small relative to the parameter count",
    "HMQC-NOISE-GROUP": f"{G}, observation error covariance: R is user-set; too small R makes the update too strong",
    "HMQC-NOISE-TRUTH": "Synthetic truth run: realized noise against the stated errors (box Drogon obs = truth + N(0, error))",
    "HMQC-NOISE-CORRELATED": f"{G}, observation error covariance: R is assumed diagonal (independent errors)",
    "HMQC-PARAM-UNCLASSIFIED": "Static (grid properties, fields) vs dynamic (relperm, PVT, contacts, multipliers) split",
    "HMQC-STATIC-DYNAMIC": f"{G}, parameter matrix: ERT updates scalar, surface and field groups; split static vs dynamic",
    "HMQC-FORECAST-SHIFT": "The update changes the forecast; report how much and from which parameter class",
    "HMQC-FORECAST-COLLAPSE": "ERT Drogon localization page: a global update reduces the spread strongly",
    "HMQC-FORECAST-TRUTH": "Synthetic truth: the posterior forecast must cover the truth",
    "HMQC-ATTRIBUTION-NONLINEAR": "One-at-a-time attribution: interaction means the effects do not add",
    "HMQC-PARAM-TRUTH-PRIOR": f"{G}, results of updating: the update cannot reach a truth outside the prior",
}


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior_qc = _load_sibling("prior_qc")
collapse_checks = _load_sibling("collapse_checks")


class InputError(ValueError):
    """Unreadable or inconsistent input: exit 1."""


def clean(value):
    """JSON-safe, deterministic: 6 significant digits, numpy to Python, NaN to None, inf to 'inf'."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.bool_ | bool):
        return bool(value)
    if isinstance(value, np.integer | int):
        return int(value)
    if isinstance(value, np.floating | float):
        value = float(value)
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return float(f"{value:.6g}")
    return value


def slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


def ratio(a, b):
    return a / b if b else None


def pct(values: np.ndarray, q: float) -> float:
    return float(np.percentile(values, q, method="linear"))


def sd(values) -> float | None:
    values = np.asarray(values, dtype=float)
    return float(np.std(values, ddof=1)) if len(values) > 1 else None


def classify(name: str, rules: list[tuple[str, str, str]]) -> tuple[str, str]:
    for klass, category, pattern in rules:
        if re.search(pattern, prior_qc.bare(name), re.I):
            return klass, category
    return "unclassified", "unclassified"


def parse_class_rule(text: str) -> tuple[str, str, str]:
    pattern, sep, target = text.rpartition("=")
    klass, colon, category = target.partition(":")
    if not sep or not pattern or not colon or klass not in ("static", "dynamic") or not category:
        raise ValueError(f"--param-class '{text}': expected REGEX=static:CATEGORY or REGEX=dynamic:CATEGORY")
    re.compile(pattern)
    return klass, category, pattern


def obs_family(key: str) -> str:
    if key.startswith("GEN:"):
        return "GEN:" + key[4:].split("_")[0]
    return re.sub(r"__\d+$", "", key.split(":")[0])


def obs_well(key: str) -> str:
    if key.startswith("GEN:"):
        return key[4:].rsplit("_", 1)[-1]
    return key.split(":", 1)[1] if ":" in key else "(none)"


def read_manifest(folder: Path) -> dict:
    file = folder / "manifest.json"
    if not file.is_file():
        raise InputError(f"{folder}: no manifest.json (give a misfit_report.py output folder)")
    return json.loads(file.read_text(encoding="utf-8"))


def read_residuals(folder: Path) -> pd.DataFrame:
    file = folder / "residuals.csv"
    if not file.is_file():
        raise InputError(f"{folder}: no residuals.csv")
    table = pd.read_csv(file, dtype={"ENSEMBLE": str, "OBS": str, "KEY": str, "DATE": str})
    need = {"ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED", "RESIDUAL"}
    if need - set(table.columns):
        raise InputError(f"{file}: missing columns {sorted(need - set(table.columns))}")
    table["DATE"] = table["DATE"].fillna("")
    return table


def require_complete(manifest: dict) -> None:
    """A misfit folder written with --allow-partial is refused: the comparison would not be like for like."""
    problems = [
        f"{item.get('ensemble')} realization {item.get('realization')}: {item.get('issue')}"
        for item in manifest.get("issues") or []
    ]
    problems += [
        f"{label}: missing realizations {sorted(info['missing'])}"
        for label, info in sorted(manifest.get("ensembles", {}).items())
        if info.get("missing")
    ]
    if problems:
        raise InputError(
            "the misfit folder is partial (written with --allow-partial):\n" + "\n".join(problems)
        )


def realizations_used(manifest: dict, label: str) -> list[int]:
    ensembles = manifest.get("ensembles", {})
    if label not in ensembles:
        raise InputError(f"ensemble '{label}' is not in the misfit manifest (has {sorted(ensembles)})")
    return sorted(int(r) for r in ensembles[label]["realizations_used"])


def read_params_csv(path: Path) -> pd.DataFrame:
    """Parameter table with a REAL column: rows are realizations, columns are native parameter values."""
    table = pd.read_csv(path)
    real = [c for c in table.columns if c.upper() == "REAL"]
    if not real:
        raise InputError(f"{path}: needs a REAL column")
    table = table.rename(columns={real[0]: "REAL"})
    if table["REAL"].duplicated().any():
        raise InputError(f"{path}: duplicate REAL values")
    table = table.set_index(table["REAL"].astype(int)).drop(columns="REAL")
    drop = [c for c in table.columns if c.startswith("LOG10_") or c.upper() in ("ENSEMBLE", "ITER", "IENS")]
    table = table.drop(columns=drop)
    for column in table.columns:
        values = pd.to_numeric(table[column], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise InputError(f"{path}: {column} has a value that is not a finite number")
        table[column] = values.astype(float)
    return table.sort_index()


def read_params_runpaths(pattern: str, expected: list[int]) -> pd.DataFrame:
    ensemble = runpath_lib.read_ensemble(pattern, expected=set(expected), need_data_file=False)
    problems = ensemble.problems()
    problems += [f"realization {item.realization} ({item.path}): {item.reason}" for item in ensemble.extra]
    if problems or not ensemble.complete:
        raise InputError(f"runpaths {pattern} do not match the misfit manifest:\n" + "\n".join(problems))
    rows = {}
    for member in ensemble.found:
        file = member.runpath / "parameters.txt"
        if not file.is_file():
            raise InputError(f"{member.runpath}: no parameters.txt")
        rows[member.realization] = {
            name: value for name, value in prior_qc.read_truth(file).items() if not name.startswith("LOG10_")
        }
    table = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    if table.isna().any().any():
        raise InputError(f"runpaths {pattern}: the realizations have different parameter sets")
    return table


def check_reals(table: pd.DataFrame, expected: list[int], what: str) -> None:
    found = sorted(int(r) for r in table.index)
    if found != expected:
        missing = sorted(set(expected) - set(found))
        extra = sorted(set(found) - set(expected))
        raise InputError(
            f"{what}: realizations differ from the misfit manifest (missing {missing}, extra {extra})"
        )


def read_dists(files: list[Path]) -> dict[str, dict]:
    params: dict[str, dict] = {}
    for path in files:
        found, errors = prior_qc.parse_dist(path)
        if errors:
            raise InputError("; ".join(f"{e['file']}:{e['line']}: {e['message']}" for e in errors))
        for p in found:
            if p["name"] in params:
                raise InputError(f"{p['name']} is defined twice in the distribution files")
            params[p["name"]] = p
    if not params:
        raise InputError("no parameter in the distribution files")
    return params


def full_names(table: pd.DataFrame, params: dict[str, dict], what: str) -> dict[str, str]:
    """Distribution name -> column name of the ensemble table. Unknown or missing names are errors."""
    matched, unknown = prior_qc.match({c: c for c in table.columns}, params)
    if unknown:
        raise InputError(f"{what}: parameters not in the distribution files: {sorted(unknown)[:10]}")
    names = {dist: column for dist, column in matched.items()}
    missing = sorted(set(params) - set(names))
    if missing:
        raise InputError(f"{what}: distribution parameters missing in the ensemble: {missing[:10]}")
    return names


def scaled(table: pd.DataFrame, params: dict[str, dict], names: dict[str, str]) -> pd.DataFrame:
    """Uncertain parameters in the sampling scale, columns named by distribution name."""
    out = {}
    for dist, p in sorted(params.items()):
        if p["distribution"] in FIXED:
            continue
        try:
            out[dist] = [prior_qc.to_scale(p, v) for v in table[names[dist]]]
        except ValueError as error:
            raise InputError(str(error)) from error
    return pd.DataFrame(out, index=table.index)


def at_bound(p: dict, post_native: np.ndarray, post_mean_native: float) -> str | None:
    if p["distribution"] in HARD_BOUND_FREE:
        return None
    places = [prior_qc.position(p, v) for v in post_native]
    mean_place = prior_qc.position(p, post_mean_native)
    for side, near in (("lower", lambda w: w <= EDGE_FRACTION), ("upper", lambda w: w >= 1 - EDGE_FRACTION)):
        share = sum(1 for w in places if w is not None and near(w)) / len(places)
        if (mean_place is not None and near(mean_place)) or share >= PILE_FRACTION:
            return side
    return None


def parameter_rows(params, prior_s, post_s, post_native, truth, rules) -> list[dict]:
    truth_values = prior_qc.match(truth, params)[0] if truth else {}
    rows = []
    for name in sorted(prior_s.columns):
        p = params[name]
        prior_values, post_values = prior_s[name].to_numpy(), post_s[name].to_numpy()
        prior_mean, post_mean = float(prior_values.mean()), float(post_values.mean())
        prior_sd, post_sd = sd(prior_values), sd(post_values)
        log = prior_qc.scale_name(p) == "log10"
        klass, category = classify(name, rules)
        row = {
            "name": name,
            "class": klass,
            "category": category,
            "distribution": p["distribution"],
            "scale": prior_qc.scale_name(p),
            "prior_mean": prior_mean,
            "prior_sd": prior_sd,
            "prior_sd_dist": prior_qc.prior_spread_from_distribution(p)[0],
            "post_mean": post_mean,
            "post_sd": post_sd,
            "sd_ratio": ratio(post_sd, prior_sd),
            "shift": ratio(post_mean - prior_mean, prior_sd),
            "post_p10": pct(post_values, 10),
            "post_p50": pct(post_values, 50),
            "post_p90": pct(post_values, 90),
            "at_bound": at_bound(p, post_native[name].to_numpy(), 10**post_mean if log else post_mean),
            "truth": None,
            "truth_scale": None,
            "truth_prior_position": None,
            "truth_in_prior_range": None,
            "truth_z_post": None,
            "truth_in_post_p10_p90": None,
            "linear_update": None,
            "subthreshold_share": None,
            "significant_obs": None,
            "driver_group": None,
            "driver_share": None,
        }
        if name in truth_values:
            row.update(truth_fields(p, truth_values[name], prior_values, post_values, post_mean, post_sd))
        rows.append(row)
    return rows


def truth_fields(p, value, prior_values, post_values, post_mean, post_sd) -> dict:
    out = {"truth": value, "truth_prior_position": prior_qc.position(p, value)}
    try:
        t = prior_qc.to_scale(p, value)
    except ValueError:
        out.update(truth_in_prior_range=False, truth_z_post=math.inf, truth_in_post_p10_p90=False)
        return out
    gap = abs(t - post_mean)
    out["truth_scale"] = t
    out["truth_in_prior_range"] = bool(prior_values.min() <= t <= prior_values.max())
    out["truth_z_post"] = gap / post_sd if post_sd else (math.inf if gap > 0 else 0.0)
    out["truth_in_post_p10_p90"] = bool(pct(post_values, 10) <= t <= pct(post_values, 90))
    return out


def prior_qc_findings(params, prior_native, post_native, prior_names, post_names, truth, args) -> list[dict]:
    """prior_qc check and spread_findings, run in-process with prior_qc's CLI defaults."""
    options = argparse.Namespace(
        collapse_ratio=args.collapse_ratio,
        collapse_share=0.5,
        truth_z=args.truth_z,
        min_realizations=20,
        edge_fraction=EDGE_FRACTION,
        pile_fraction=PILE_FRACTION,
        log_pattern=prior_qc.DEFAULT_LOG_PATTERN,
        not_log_pattern=prior_qc.DEFAULT_NOT_LOG_PATTERN,
    )
    posterior = {post_names[n]: post_native[post_names[n]].tolist() for n in params}
    prior = {prior_names[n]: prior_native[prior_names[n]].tolist() for n in params}
    n = min(len(prior_native), len(post_native))
    found, errors = prior_qc.check(params, truth, posterior, n, options)
    if errors:
        raise InputError("; ".join(e["message"] for e in errors))
    rows = prior_qc.spread_table(params, posterior, prior, truth)
    uncertain = sum(1 for p in params.values() if p["distribution"] not in FIXED)
    return found + prior_qc.spread_findings(rows, n, uncertain, options)


def obs_table(res: pd.DataFrame, prior_label: str, post_label: str, pp_z: float) -> pd.DataFrame:
    """One row per observation: groups, prior and posterior statistics, prior-predictive z."""
    finite = res[np.isfinite(res["RESIDUAL"]) & np.isfinite(res["SIMULATED"])]
    first = res.drop_duplicates("OBS").set_index("OBS")[["KEY", "DATE", "OBSERVED", "ERROR"]]
    table = first.copy()
    table["family"] = table["KEY"].map(obs_family)
    table["well"] = table["KEY"].map(obs_well)
    for label, tag in ((prior_label, "prior"), (post_label, "post")):
        rows = finite[finite["ENSEMBLE"] == label].groupby("OBS")
        table[f"{tag}_n"] = rows["RESIDUAL"].count()
        table[f"{tag}_mean_sim"] = rows["SIMULATED"].mean()
        table[f"{tag}_sd_sim"] = rows["SIMULATED"].std(ddof=1)
        table[f"{tag}_mean_res"] = rows["RESIDUAL"].mean()
        table[f"{tag}_msr"] = rows["RESIDUAL"].apply(lambda r: float((r**2).mean()))
    spread = np.sqrt(table["prior_sd_sim"] ** 2 + table["ERROR"] ** 2)
    table["z_pp"] = (table["OBSERVED"] - table["prior_mean_sim"]) / spread
    table["covered"] = table["z_pp"].abs() <= pp_z
    return table.sort_index()


def group_misfit(res: pd.DataFrame, obs: pd.DataFrame, label: str, column: str) -> dict:
    rows = res[(res["ENSEMBLE"] == label) & np.isfinite(res["RESIDUAL"])]
    groups = rows["OBS"].map(obs[column])
    return (rows["RESIDUAL"] ** 2).groupby(groups).mean().to_dict()


def observation_groups(res, obs, prior_label, post_label) -> list[dict]:
    rows = []
    for level, column in (("family", "family"), ("key", "KEY"), ("well", "well")):
        prior = group_misfit(res, obs, prior_label, column)
        post = group_misfit(res, obs, post_label, column)
        for group, members in obs.groupby(column):
            scored = members[members["z_pp"].notna()]
            n_post = int((members["post_n"].fillna(0) > 0).sum())
            rows.append(
                {
                    "level": level,
                    "group": group,
                    "n_obs": len(members),
                    "prior_misfit": prior.get(group),
                    "post_misfit": post.get(group),
                    "misfit_change_ratio": ratio(post.get(group), prior.get(group)),
                    "chi2_band": collapse_checks.chi2_band(n_post),
                    "pp_covered_share": float(scored["covered"].mean()) if len(scored) else None,
                }
            )
    return sorted(rows, key=lambda r: (LEVELS.index(r["level"]), r["group"]))


def obs_lists(obs: pd.DataFrame, args) -> dict:
    scored = obs[obs["z_pp"].notna()]
    uncovered = scored[~scored["covered"]].assign(absz=lambda t: t["z_pp"].abs())
    uncovered = uncovered.sort_values(["absz"], ascending=False, kind="stable")
    outliers = obs[obs["post_mean_res"].abs() > args.outlier_z]
    outliers = outliers.assign(absr=outliers["post_mean_res"].abs()).sort_values(
        "absr", ascending=False, kind="stable"
    )
    worse = obs[(obs["post_msr"] > obs["prior_msr"]) & (obs["post_msr"] > 1)]
    worse = worse.assign(gain=worse["post_msr"] - worse["prior_msr"]).sort_values(
        "gain", ascending=False, kind="stable"
    )

    def items(table, fields):
        return [{"obs": o, **{f: r[f] for f in fields}} for o, r in table.head(TOP).iterrows()]

    return {
        "prior_predictive": {
            "pp_z": args.pp_z,
            "n_scored": len(scored),
            "covered_share": float(scored["covered"].mean()) if len(scored) else None,
            "n_uncovered": len(uncovered),
            "uncovered": items(
                uncovered, ["KEY", "DATE", "OBSERVED", "prior_mean_sim", "prior_sd_sim", "z_pp"]
            ),
            "sorted_by": "|z_pp| descending",
        },
        "outliers": {
            "limit": args.outlier_z,
            "count": len(outliers),
            "top": items(outliers, ["KEY", "DATE", "prior_mean_res", "post_mean_res"]),
            "sorted_by": "|posterior mean residual| descending",
        },
        "worsened": {
            "count": len(worse),
            "top": items(worse, ["KEY", "DATE", "prior_msr", "post_msr"]),
            "sorted_by": "posterior minus prior mean squared residual, descending",
        },
    }


def kalman_gain(a: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """K = C_md (C_dd + I)^-1 and diag(C_dd (C_dd + I)^-1), in ensemble space (Woodbury, no m x m inverse)."""
    c = a.shape[0] - 1
    g = np.linalg.solve(d @ d.T + c * np.eye(a.shape[0]), d)
    k = a.T @ (d - (d @ d.T) @ g) / c
    s = np.einsum("nj,nj->j", d, g)
    return k, s


def influence(prior_s: pd.DataFrame, res: pd.DataFrame, obs: pd.DataFrame, prior_label: str) -> dict:
    stds = prior_s.std(ddof=1)
    dropped = sorted(stds[~(stds > 0)].index)
    x = prior_s.drop(columns=dropped)
    x = (x - x.mean()) / x.std(ddof=1)
    rows = res[res["ENSEMBLE"] == prior_label]
    sim = rows.pivot(index="REAL", columns="OBS", values="SIMULATED").reindex(x.index)
    keep = sorted(c for c in sim.columns if np.isfinite(sim[c].to_numpy(dtype=float)).all())
    excluded = len(obs) - len(keep)
    error = obs.loc[keep, "ERROR"].to_numpy(dtype=float)
    y = sim[keep].to_numpy(dtype=float) / error
    a = x.to_numpy() - x.to_numpy().mean(axis=0)
    d = y - y.mean(axis=0)
    k, s = kalman_gain(a, d)
    e = (obs.loc[keep, "OBSERVED"].to_numpy() - sim[keep].to_numpy().mean(axis=0)) / error
    w = k * e
    rho = correlation(a, d)
    return {
        "params": list(x.columns),
        "obs": keep,
        "dropped_params": dropped,
        "excluded_obs": excluded,
        "k": k,
        "s": s,
        "e": e,
        "w": w,
        "rho": rho,
        "threshold": 3 / math.sqrt(len(x)) if len(x) else None,
        "n": len(x),
        "p": x.shape[1],
        "m": len(keep),
        "rank": int(np.linalg.matrix_rank(d)) if d.size else 0,
    }


def correlation(a: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Sample correlation of each parameter with each response (p x m); 0 where a response has no spread."""
    sa = np.sqrt((a**2).sum(axis=0))
    sd_ = np.sqrt((d**2).sum(axis=0))
    with np.errstate(divide="ignore", invalid="ignore"):
        rho = (a.T @ d) / np.outer(sa, sd_)
    return np.nan_to_num(rho)


def subthreshold_share(inf: dict, i: int) -> float | None:
    """Share of the |contributions| to parameter i from responses with |correlation| below 3/sqrt(N)."""
    size = np.abs(inf["w"][i])
    total = float(size.sum())
    if total == 0 or inf["threshold"] is None:
        return None
    return float(size[np.abs(inf["rho"][i]) < inf["threshold"]].sum() / total)


def group_vectors(inf: dict, obs: pd.DataFrame, column: str) -> dict[str, np.ndarray]:
    labels = obs.loc[inf["obs"], column].to_numpy()
    return {g: inf["w"][:, labels == g].sum(axis=1) for g in sorted(set(labels))}


def group_influence(inf: dict, obs: pd.DataFrame) -> list[dict]:
    rows = []
    dfs_total = float(inf["s"].sum())
    for level, column in (("family", "family"), ("key", "KEY"), ("well", "well")):
        labels = obs.loc[inf["obs"], column].to_numpy()
        vectors = group_vectors(inf, obs, column)
        norms = {g: float(np.linalg.norm(v)) for g, v in vectors.items()}
        norm_total = sum(norms.values())
        for g, v in vectors.items():
            dfs = float(inf["s"][labels == g].sum())
            order = sorted(range(len(v)), key=lambda i: (-abs(v[i]), inf["params"][i]))[:3]
            rows.append(
                {
                    "level": level,
                    "group": g,
                    "dfs": dfs,
                    "dfs_share": ratio(dfs, dfs_total),
                    "update_norm": norms[g],
                    "update_share": ratio(norms[g], norm_total),
                    "top_params": [{"name": inf["params"][i], "push": float(v[i])} for i in order],
                }
            )
    return sorted(rows, key=lambda r: (LEVELS.index(r["level"]), r["group"]))


def parameter_influence(inf: dict, obs: pd.DataFrame) -> dict[str, dict]:
    families = group_vectors(inf, obs, "family")
    linear = inf["w"].sum(axis=1)
    out = {}
    for i, name in enumerate(inf["params"]):
        contributions = {g: float(v[i]) for g, v in families.items()}
        size = sum(abs(c) for c in contributions.values())
        driver = min(contributions, key=lambda g: (-abs(contributions[g]), g)) if contributions else None
        positive = sum(c for c in contributions.values() if c > 0)
        negative = -sum(c for c in contributions.values() if c < 0)
        out[name] = {
            "linear_update": float(linear[i]),
            "subthreshold_share": subthreshold_share(inf, i),
            "significant_obs": int((np.abs(inf["rho"][i]) >= inf["threshold"]).sum())
            if inf["threshold"]
            else 0,
            "by_family": contributions,
            "driver_group": driver,
            "driver_share": ratio(abs(contributions[driver]), size) if driver else None,
            "push_up": positive,
            "push_down": negative,
            "tug": ratio(min(positive, negative), max(positive, negative)) or 0.0,
        }
    return out


def closure(rows: list[dict], args) -> dict:
    pairs = [(r["name"], r["shift"], r["linear_update"]) for r in rows if r["linear_update"] is not None]
    pairs = [p for p in pairs if p[1] is not None]
    shifts = np.array([p[1] for p in pairs])
    linear = np.array([p[2] for p in pairs])
    corr = None
    if len(pairs) > 2 and shifts.std() > 0 and linear.std() > 0:
        corr = float(np.corrcoef(shifts, linear)[0, 1])
    sub = {r["name"]: r.get("subthreshold_share") for r in rows}
    unexplained = [
        {"name": n, "shift": s, "linear_update": u, "subthreshold_share": sub.get(n)}
        for n, s, u in pairs
        if abs(s) > args.spurious_shift
        and (
            abs(u) < args.spurious_linear
            or s * u < 0
            or (sub.get(n) is not None and sub[n] >= args.spurious_subshare)
        )
    ]
    return {
        "sign_agreement_share": float(np.mean(np.sign(shifts) == np.sign(linear))) if pairs else None,
        "pearson_shift_vs_linear": corr,
        "unexplained": unexplained,
        "unexplained_rule": (
            f"|shift| > {args.spurious_shift:g} and (|linear_update| < {args.spurious_linear:g}, or opposite sign,"
            f" or >= {args.spurious_subshare:g} of the linear update from responses with |correlation| < 3/sqrt(N))"
        ),
    }


def pair_conflicts(inf: dict, obs: pd.DataFrame, args) -> list[dict]:
    out = []
    for level, column in (("family", "family"), ("key", "KEY")):
        vectors = group_vectors(inf, obs, column)
        norms = {g: float(np.linalg.norm(v)) for g, v in vectors.items()}
        floor = args.conflict_min_share * max(norms.values(), default=0.0)
        strong = sorted(g for g in vectors if norms[g] > 0 and norms[g] >= floor)
        for i, a in enumerate(strong):
            for b in strong[i + 1 :]:
                u, v = vectors[a], vectors[b]
                cosine = float(u @ v / (norms[a] * norms[b]))
                if cosine > args.conflict_cos:
                    continue
                opposite = [j for j in range(len(u)) if u[j] * v[j] < 0]
                opposite.sort(key=lambda j: (-min(abs(u[j]), abs(v[j])), inf["params"][j]))
                out.append(
                    {
                        "level": level,
                        "a": a,
                        "b": b,
                        "cosine": cosine,
                        "params": [
                            {"name": inf["params"][j], "a": float(u[j]), "b": float(v[j])}
                            for j in opposite[:3]
                        ],
                    }
                )
    return sorted(out, key=lambda r: (r["cosine"], r["level"], r["a"], r["b"]))


def band_verdict(misfit, band) -> str:
    if misfit is None or band is None:
        return "unavailable"
    if misfit < band[0]:
        return "below band"
    return "above band" if misfit > band[1] else "inside"


def lag1(values: np.ndarray) -> float | None:
    centred = values - values.mean()
    denominator = float(centred @ centred)
    return float(centred[:-1] @ centred[1:] / denominator) if denominator > 0 else None


def noise(groups: list[dict], obs: pd.DataFrame, truth_res: pd.DataFrame | None, args) -> dict:
    per_group = [
        {
            "level": g["level"],
            "group": g["group"],
            "post_misfit": g["post_misfit"],
            "chi2_band": g["chi2_band"],
            "verdict": band_verdict(g["post_misfit"], g["chi2_band"]),
        }
        for g in groups
        if g["level"] in ("family", "key")
    ]
    autocorr = []
    for key, members in obs[~obs["KEY"].str.startswith("GEN:")].groupby("KEY"):
        dated = members[(members["DATE"] != "") & members["post_mean_res"].notna()].sort_values("DATE")
        if len(dated) >= 6:
            value = lag1(dated["post_mean_res"].to_numpy(dtype=float))
            autocorr.append({"key": key, "n_obs": len(dated), "lag1": value})
    relative = []
    for key, members in obs.groupby("KEY"):
        nonzero = members[members["OBSERVED"] != 0]
        if len(nonzero):
            relative.append(
                {
                    "key": key,
                    "median_error_over_observed": float(
                        (nonzero["ERROR"] / nonzero["OBSERVED"].abs()).median()
                    ),
                }
            )
    return {
        "groups": per_group,
        "truth_run": truth_noise(truth_res),
        "autocorrelation": {"limit": args.autocorr_limit, "min_obs": 6, "keys": autocorr},
        "relative_error": relative,
    }


def truth_noise(res: pd.DataFrame | None) -> dict | None:
    if res is None:
        return None
    labels = sorted(res["ENSEMBLE"].unique())
    label = labels[0] if len(labels) == 1 else "truth" if "truth" in labels else labels[0]
    rows = res[(res["ENSEMBLE"] == label) & np.isfinite(res["RESIDUAL"])]
    families = []
    for family, members in rows.groupby(rows["KEY"].map(obs_family)):
        n_obs = members["OBS"].nunique()
        misfit = float((members["RESIDUAL"] ** 2).mean())
        band = collapse_checks.chi2_band(n_obs)
        families.append(
            {
                "family": family,
                "n_obs": n_obs,
                "misfit": misfit,
                "chi2_band": band,
                "verdict": band_verdict(misfit, band),
            }
        )
    return {"ensemble": label, "families": families}


def read_forecast(path: Path, expected: list[int]) -> dict[str, pd.Series]:
    table = pd.read_csv(path, dtype={"KEY": str, "AS_OF": str})
    need = {"REAL", "AS_OF", "KEY", "VALUE"}
    if need - set(table.columns):
        raise InputError(f"{path}: missing columns {sorted(need - set(table.columns))}")
    if table.duplicated(["REAL", "KEY", "AS_OF"]).any():
        raise InputError(f"{path}: duplicate REAL, KEY, AS_OF rows")
    if not np.isfinite(pd.to_numeric(table["VALUE"], errors="coerce").to_numpy(dtype=float)).all():
        raise InputError(f"{path}: VALUE has a value that is not a finite number")
    out = {}
    for (key, as_of), rows in table.groupby(["KEY", "AS_OF"]):
        series = rows.set_index(rows["REAL"].astype(int))["VALUE"].astype(float).sort_index()
        check_reals(series.to_frame(), expected, f"{path} {key}@{as_of}")
        out[f"{key}@{as_of}"] = series
    return out


def parse_forecast_truth(items: list[str], ids: list[str]) -> dict[str, float]:
    out = {}
    for item in items:
        key, sep, value = item.rpartition("=")
        if not sep:
            raise InputError(f"--forecast-truth '{item}': expected KEY=VALUE or KEY@AS_OF=VALUE")
        hits = [i for i in ids if i == key or i.split("@", 1)[0] == key]
        if not hits:
            raise InputError(f"--forecast-truth {key}: not in the forecast tables")
        for hit in hits:
            out[hit] = float(value)
    return out


def stats(values: np.ndarray) -> dict:
    return {
        "n": len(values),
        "mean": float(values.mean()),
        "sd": sd(values),
        "p10": pct(values, 10),
        "p50": pct(values, 50),
        "p90": pct(values, 90),
    }


def require_same_forecast_ids(prior_f: dict, post_f: dict) -> None:
    prior_ids, post_ids = set(prior_f), set(post_f)
    if prior_ids != post_ids:
        raise InputError(
            "forecast identities differ: "
            f"prior only {sorted(prior_ids - post_ids) or 'none'}; "
            f"posterior only {sorted(post_ids - prior_ids) or 'none'}"
        )


def forecast(prior_f, post_f, truth, prior_s, post_s, classes) -> list[dict]:
    rows = []
    for fid in sorted(set(prior_f) & set(post_f)):
        a, b = prior_f[fid].to_numpy(), post_f[fid].to_numpy()
        pa, pb = stats(a), stats(b)
        row = {
            "id": fid,
            "prior": pa,
            "posterior": pb,
            "sd_ratio": ratio(pb["sd"], pa["sd"]),
            "shift_in_prior_sd": ratio(pb["mean"] - pa["mean"], pa["sd"]),
            "post_mean_outside_prior_p10_p90": not pa["p10"] <= pb["mean"] <= pa["p90"],
            "truth": truth.get(fid),
            "truth_in_post_p10_p90": None,
            "truth_in_prior_p10_p90": None,
            "statistical_attribution": statistical_attribution(prior_f[fid], prior_s, post_s, classes),
        }
        if fid in truth:
            row["truth_in_post_p10_p90"] = pb["p10"] <= truth[fid] <= pb["p90"]
            row["truth_in_prior_p10_p90"] = pa["p10"] <= truth[fid] <= pa["p90"]
        row["statistical_attribution"]["actual_change"] = pb["mean"] - pa["mean"]
        actual = pb["mean"] - pa["mean"]
        row["statistical_attribution"]["closure"] = (
            row["statistical_attribution"]["linear_sum"] / actual if actual != 0 else None
        )
        rows.append(row)
    return rows


def statistical_attribution(f: pd.Series, prior_s: pd.DataFrame, post_s: pd.DataFrame, classes) -> dict:
    x = prior_s.loc[f.index]
    fv = f.to_numpy()
    contributions, by_class, by_category = {}, {}, {}
    for name in sorted(x.columns):
        xi = x[name].to_numpy()
        var = xi.var(ddof=1)
        if not var > 0:
            continue
        b = float(np.cov(fv, xi, ddof=1)[0, 1] / var)
        c = b * float(post_s[name].mean() - prior_s[name].mean())
        contributions[name] = c
        klass, category = classes[name]
        by_class[klass] = by_class.get(klass, 0.0) + c
        by_category[f"{klass}:{category}"] = by_category.get(f"{klass}:{category}", 0.0) + c
    return {
        "method": "marginal-regression estimate",
        "contributions": contributions,
        "by_class": by_class,
        "by_category": by_category,
        "linear_sum": sum(contributions.values()),
        "r2_linear_prior": r2_linear(x.to_numpy(), fv),
        "limiter": "Assumes independent prior parameters and a linear response; uses the prior ensemble only;"
        " no simulation runs. Compare with the run-based attribution when it exists",
    }


def r2_linear(x: np.ndarray, f: np.ndarray) -> float | None:
    """R2 of a least-squares linear fit of f on the prior parameters. None when N <= p + 1 or f is constant."""
    total = float(((f - f.mean()) ** 2).sum())
    if len(f) <= x.shape[1] + 1 or not total > 0:
        return None
    design = np.column_stack([np.ones(len(f)), x])
    fitted = design @ np.linalg.lstsq(design, f, rcond=None)[0]
    return 1 - float(((f - fitted) ** 2).sum()) / total


def read_attribution(path: Path) -> list[dict]:
    table = pd.read_csv(path, dtype={"CASE": str, "KEY": str})
    if {"CASE", "KEY", "VALUE"} - set(table.columns):
        raise InputError(f"{path}: needs CASE, KEY, VALUE")
    rows = []
    for key, group in table.groupby("KEY"):
        values = dict(zip(group["CASE"].str.lower(), group["VALUE"].astype(float), strict=True))
        if sorted(values) != sorted(CASES) or len(group) != 4:
            raise InputError(f"{path}: {key} needs exactly one row for each case {list(CASES)}")
        base, static, dynamic, everything = (values[c] for c in CASES)
        total = everything - base
        effects = {
            "static_effect": static - base,
            "dynamic_effect": dynamic - base,
            "interaction": everything - static - dynamic + base,
        }
        row = {"key": key, **values, **effects, "total": total}
        for name, value in effects.items():
            row[f"{name}_share"] = ratio(value, abs(total))
        rows.append(row)
    return rows


def write_attribution(folder: Path, params, prior_s, post_s, names, prior_native, rules) -> None:
    cases = {case: {} for case in CASES}
    plan = {}
    for dist in sorted(params):
        p = params[dist]
        full = names[dist]
        if p["distribution"] in FIXED:
            base = post = float(prior_native[full].mean())
        else:
            log = prior_qc.scale_name(p) == "log10"
            base, post = (float(t[dist].mean()) for t in (prior_s, post_s))
            base, post = (10**base, 10**post) if log else (base, post)
        klass, category = classify(dist, rules)
        cases["base"][full] = base
        cases["static"][full] = post if klass == "static" else base
        cases["dynamic"][full] = post if klass in ("dynamic", "unclassified") else base
        cases["all"][full] = post
        plan[full] = {"class": klass, "category": category, "base": base, "post": post}
    for case, values in cases.items():
        target = folder / case
        target.mkdir(parents=True, exist_ok=True)
        lines = [f"{name} {values[name]:.8g}" for name in sorted(values)]
        (target / "parameters.txt").write_text("\n".join(lines) + "\n")
    document = {
        "schema_version": SCHEMA,
        "base": "prior ensemble mean in the sampling scale, back-transformed (10**m for log10)",
        "post": "posterior ensemble mean in the sampling scale, back-transformed",
        "cases": {
            "base": "all parameters at base",
            "static": "static parameters at post, others at base",
            "dynamic": "dynamic and unclassified parameters at post, others at base",
            "all": "all parameters at post",
        },
        "parameters": plan,
    }
    (folder / "attribution_plan.json").write_text(
        json.dumps(clean(document), indent=2, sort_keys=True) + "\n"
    )


def static_dynamic(rows: list[dict], fields: list[dict], args) -> dict:
    total = sum(abs(r["shift"]) for r in rows if r["shift"] is not None)

    def summary(members):
        shifts = [abs(r["shift"]) for r in members if r["shift"] is not None]
        ratios = [r["sd_ratio"] for r in members if r["sd_ratio"] is not None]
        return {
            "n_params": len(members),
            "mean_abs_shift": float(np.mean(shifts)) if shifts else None,
            "median_sd_ratio": float(np.median(ratios)) if ratios else None,
            "n_collapsed": sum(1 for v in ratios if v < args.collapse_ratio),
            "n_truth_outside": sum(1 for r in members if is_number(r["truth_z_post"], args.truth_z)),
            "share_of_update": ratio(sum(shifts), total),
        }

    classes = {}
    for klass in ("static", "dynamic", "unclassified"):
        members = [r for r in rows if r["class"] == klass]
        categories = sorted({r["category"] for r in members})
        classes[klass] = {
            **summary(members),
            "categories": {c: summary([r for r in members if r["category"] == c]) for c in categories},
        }
    classes["static"]["fields"] = fields
    return classes


def is_number(value, above: float) -> bool:
    return value is not None and value > above


def read_field_qc(path: Path, rules) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("keyword", "sd_ratio", "active_cells"):
        if key not in data:
            raise InputError(f"{path}: not a field_qc.py JSON (no '{key}')")
    klass, category = classify(data["keyword"], rules)
    truth = data.get("truth") or {}
    active = data["active_cells"]
    return {
        "file": str(path),
        "keyword": data["keyword"],
        "class": "static",
        "category": category if klass == "static" else "field",
        "scale": data.get("scale"),
        "sd_ratio": data["sd_ratio"],
        "collapsed_cells": data.get("collapsed_cells"),
        "collapsed_share": ratio(data.get("collapsed_cells") or 0, active),
        "outside_cells": data.get("outside_cells"),
        "active_cells": active,
        "truth_rmse_prior": (truth.get("prior") or {}).get("rmse"),
        "truth_rmse_post": (truth.get("posterior") or {}).get("rmse"),
        "truth_coverage_prior": (truth.get("prior") or {}).get("coverage_p5_p95"),
        "truth_coverage_post": (truth.get("posterior") or {}).get("coverage_p5_p95"),
        "findings": data.get("findings", []),
    }


def check(
    cid, fired, headline, numbers=None, threshold=None, limiter=None, examples=None, severity="warning"
):
    return {
        "id": cid,
        "source": "ahm_overview",
        "severity": severity,
        "fired": bool(fired),
        "headline": headline,
        "numbers": numbers or {},
        "threshold": threshold,
        "limiter": limiter,
        "rationale": RATIONALE[cid],
        "examples": (examples or [])[:EXAMPLES],
    }


def copied(source: str, cid: str, headline: str, detail, examples) -> dict:
    return {
        "id": cid,
        "source": source,
        "severity": "warning",
        "fired": True,
        "headline": headline,
        "numbers": {"detail": detail},
        "threshold": None,
        "limiter": None,
        "rationale": f"copied from {source}.py",
        "examples": [str(e) for e in (examples or [])][:EXAMPLES],
    }


def existing_checks(manifest: dict, prior_found: list[dict], fields: list[dict]) -> list[dict]:
    out = []
    for item in manifest.get("findings", []):
        cid = item.get("code") or "MISFIT:" + slug(item["finding"])
        where = f" ({item['ensemble']})" if item.get("ensemble") else ""
        out.append(
            copied("misfit_report", cid, item["finding"] + where, item.get("detail"), item.get("examples"))
        )
    grouped: dict[str, list[dict]] = {}
    for item in prior_found:
        grouped.setdefault(item.get("code") or "PRIOR:" + slug(item["finding"]), []).append(item)
    for cid, items in sorted(grouped.items()):
        names = [i["parameter"] for i in items if i.get("parameter")]
        headline = items[0]["finding"] + (f" ({len(items)} parameters)" if len(names) > 1 else "")
        examples = [f"{i['parameter'] or '-'}: {i['detail']}" for i in items]
        out.append(
            copied("prior_qc", cid, headline, items[0]["detail"] if len(items) == 1 else None, examples)
        )
    for field in fields:
        for item in field["findings"]:
            cid = item.get("code") or "FIELD:" + slug(item["finding"])
            out.append(
                copied("field_qc", cid, f"{field['keyword']}: {item['finding']}", item.get("detail"), [])
            )
    return out


def new_checks(ctx: dict, args) -> list[dict]:
    out = []
    groups, inf, rows = ctx["groups"], ctx["influence"], ctx["parameters"]
    pp = ctx["observations"]["prior_predictive"]
    fam = [g for g in groups if g["level"] == "family"]
    low_fam = [
        g["group"]
        for g in fam
        if g["pp_covered_share"] is not None and g["pp_covered_share"] < args.pp_group_share
    ]
    overall = pp["covered_share"]
    out.append(
        check(
            "HMQC-PRIOR-PREDICTIVE",
            (overall is not None and overall < args.pp_share) or low_fam,
            f"prior covers {fmt_pct(overall)} of the observations (|z_pp| <= {args.pp_z:g});"
            f" {len(low_fam)} families below {args.pp_group_share:.0%}",
            {"covered_share": overall, "n_uncovered": pp["n_uncovered"], "families_below": low_fam},
            f"overall < {args.pp_share:g} or a family < {args.pp_group_share:g}",
            "z_pp uses the prior ensemble mean and sd and the observation error; a small N makes the sd noisy",
            [f"{u['obs']} z={u['z_pp']:.3g}" for u in pp["uncovered"]],
        )
    )
    outl = ctx["observations"]["outliers"]
    out.append(
        check(
            "HMQC-OBS-OUTLIER",
            outl["count"] > 0,
            f"{outl['count']} observations have abs(posterior mean residual) > {args.outlier_z:g}",
            {"count": outl["count"]},
            f"|posterior mean residual| > {args.outlier_z:g}",
            "a model error and a data error look the same here",
            [f"{o['obs']} {o['prior_mean_res']:.3g} -> {o['post_mean_res']:.3g}" for o in outl["top"]],
        )
    )
    fam_inf = [g for g in ctx["groups_influence"] if g["level"] == "family"]
    dominant = [g for g in fam_inf if max(g["update_share"] or 0, g["dfs_share"] or 0) > args.dominant_share]
    out.append(
        check(
            "HMQC-OBS-DOMINANT",
            dominant,
            f"{len(dominant)} observation families carry more than {args.dominant_share:.0%} of the update or DFS",
            {g["group"]: {"update_share": g["update_share"], "dfs_share": g["dfs_share"]} for g in fam_inf},
            f"update_share or dfs_share > {args.dominant_share:g}",
            "linear-Gaussian estimate from the prior ensemble, one ES step, no localization",
            [g["group"] for g in dominant],
        )
    )
    conflicts = ctx["conflicts"]
    out.append(
        check(
            "HMQC-OBS-CONFLICT",
            conflicts and inf["p"] >= MIN_CONFLICT_PARAMS,
            f"{len(conflicts)} pairs of observation groups pull the parameters in opposite directions",
            {"count": len(conflicts), "families": sum(1 for c in conflicts if c["level"] == "family")},
            f"cosine <= {args.conflict_cos:g} for groups with update_norm >= {args.conflict_min_share:g} x max",
            "linear-Gaussian estimate; a conflict can be a model error, a data error, or a wrong error level"
            + (
                f"; not fired with fewer than {MIN_CONFLICT_PARAMS} scalar parameters (directions are trivial)"
                if inf["p"] < MIN_CONFLICT_PARAMS
                else ""
            ),
            [f"{c['level']} {c['a']} vs {c['b']}: cos={c['cosine']:.2f}" for c in conflicts],
        )
    )
    tugs = ctx["tug_of_war"]
    out.append(
        check(
            "HMQC-PARAM-TUG",
            tugs,
            f"{len(tugs)} parameters are pulled both ways by different observation families",
            {"count": len(tugs)},
            "tug >= 0.5 and both sides >= 0.5 prior sd",
            "linear-Gaussian estimate from the prior ensemble",
            [f"{t['name']} +{t['push_up']:.2f}/-{t['push_down']:.2f}" for t in tugs],
            severity="info",
        )
    )
    unexplained = ctx["closure"]["unexplained"]
    threshold = 3 / math.sqrt(inf["n"])
    out.append(
        check(
            "HMQC-SPURIOUS-UPDATE",
            unexplained,
            f"{len(unexplained)} parameters moved more than {args.spurious_shift:g} prior sd without a significant"
            " linear data signal",
            {"count": len(unexplained), "n": inf["n"], "localization_threshold_3_over_sqrt_n": threshold},
            ctx["closure"]["unexplained_rule"],
            "the linear estimate is one ES step from the prior; ES-MDA and nonlinear responses can explain a shift",
            [
                f"{u['name']} shift={u['shift']:.2f} linear={u['linear_update']:.2f}"
                f" sub-threshold={fmt(u['subthreshold_share'], '.0%')}"
                for u in unexplained
            ],
        )
    )
    out.append(
        check(
            "HMQC-INFLUENCE-RANK",
            inf["n"] <= inf["p"],
            f"N = {inf['n']} for {inf['p']} parameters (rank of D {inf['rank']})",
            {"n": inf["n"], "p": inf["p"], "m": inf["m"], "rank_d": inf["rank"]},
            "N <= p",
            "with N <= p the influence estimate and the update are rank deficient",
        )
    )
    below = [
        g["group"] for g in ctx["noise"]["groups"] if g["level"] == "family" and g["verdict"] == "below band"
    ]
    above = [
        g["group"] for g in ctx["noise"]["groups"] if g["level"] == "family" and g["verdict"] == "above band"
    ]
    out.append(
        check(
            "HMQC-NOISE-GROUP",
            below,
            f"{len(below)} families have a posterior misfit below the chi-square band; {len(above)} above",
            {"below_band": below, "above_band": above},
            "posterior misfit < chi2_band low (3 sigma)",
            "correlated errors widen the band; below the band can be overfit or errors too large",
            below,
        )
    )
    truth_run = ctx["noise"]["truth_run"]
    if truth_run is None:
        out.append(check("HMQC-NOISE-TRUTH", False, "no truth run given", {}, None, "no truth run"))
    else:
        outside = [f for f in truth_run["families"] if f["verdict"] in ("below band", "above band")]
        out.append(
            check(
                "HMQC-NOISE-TRUTH",
                outside,
                f"{len(outside)} families of the truth run are outside the chi-square band",
                {f["family"]: f["misfit"] for f in truth_run["families"]},
                "truth-run family misfit outside chi2_band (3 sigma)",
                "synthetic cases only; one noise realization",
                [f"{f['family']} {f['misfit']:.3g} {f['verdict']}" for f in outside],
            )
        )
    corr = [k for k in ctx["noise"]["autocorrelation"]["keys"] if is_number(k["lag1"], args.autocorr_limit)]
    out.append(
        check(
            "HMQC-NOISE-CORRELATED",
            corr,
            f"{len(corr)} keys have lag-1 autocorrelation of the posterior mean residual > {args.autocorr_limit:g}",
            {"count": len(corr)},
            f"lag-1 autocorrelation > {args.autocorr_limit:g}, keys with >= 6 dated observations",
            "a trend in the residual is also a sign of a model error, not only of correlated noise",
            [f"{k['key']} r1={k['lag1']:.2f}" for k in corr],
        )
    )
    unclassified = [r["name"] for r in rows if r["class"] == "unclassified"]
    out.append(
        check(
            "HMQC-PARAM-UNCLASSIFIED",
            unclassified,
            f"{len(unclassified)} parameters match no static or dynamic rule",
            {"count": len(unclassified)},
            "no rule matches; give --param-class REGEX=static:CATEGORY",
            "the attribution puts unclassified parameters with the dynamic case",
            unclassified,
        )
    )
    sd_ = ctx["static_dynamic"]
    shares = {k: sd_[k]["share_of_update"] for k in ("static", "dynamic", "unclassified")}
    lead = max(shares, key=lambda k: (shares[k] or 0, k))
    out.append(
        check(
            "HMQC-STATIC-DYNAMIC",
            True,
            f"{lead} parameters carry {fmt_pct(shares[lead])} of the parameter shift",
            shares,
            None,
            "share of the sum of |shift| in prior sd; fields are not in the share",
            severity="info",
        )
    )
    fc = ctx["forecast"]
    shifted = [f["id"] for f in fc if f["post_mean_outside_prior_p10_p90"]]
    out.append(
        check(
            "HMQC-FORECAST-SHIFT",
            shifted,
            f"{len(shifted)} of {len(fc)} forecast items have the posterior mean outside the prior P10-P90",
            {"count": len(shifted), "items": len(fc)},
            "posterior mean outside prior P10-P90",
            "no forecast given" if not fc else None,
            shifted,
            severity="info",
        )
    )
    level = ctx["posterior_trust"]
    collapsed = [f["id"] for f in fc if is_below(f["sd_ratio"], args.collapse_ratio)]
    out.append(
        check(
            "HMQC-FORECAST-COLLAPSE",
            collapsed and level in ("weak", "untrustworthy"),
            f"{len(collapsed)} forecast items have sd ratio < {args.collapse_ratio:g}; posterior trust {level}",
            {"count": len(collapsed), "trust": level},
            f"sd_ratio < {args.collapse_ratio:g} and trust weak or untrustworthy",
            "no forecast given" if not fc else "a resolved forecast can collapse correctly with a large N",
            collapsed,
        )
    )
    missed = [f["id"] for f in fc if f["truth_in_post_p10_p90"] is False]
    out.append(
        check(
            "HMQC-FORECAST-TRUTH",
            missed,
            f"{len(missed)} forecast truths are outside the posterior P10-P90",
            {"count": len(missed), "with_truth": sum(1 for f in fc if f["truth"] is not None)},
            "truth outside posterior P10-P90",
            "synthetic cases only; 20 % of the truths fall outside P10-P90 by chance",
            missed,
        )
    )
    nonlinear = [
        a["key"]
        for a in ctx["attribution"]
        if abs(a["interaction"]) > args.interaction_share * abs(a["total"])
    ]
    bad_closure = [
        f["id"]
        for f in fc
        if not ctx["fields"]
        and f["statistical_attribution"]["closure"] is not None
        and not 0.5 <= f["statistical_attribution"]["closure"] <= 1.5
    ]
    out.append(
        check(
            "HMQC-ATTRIBUTION-NONLINEAR",
            nonlinear or bad_closure,
            f"{len(nonlinear)} keys with a large static x dynamic interaction; {len(bad_closure)} forecast items"
            " with statistical closure outside 0.5-1.5",
            {"interaction_keys": nonlinear, "closure_outside": bad_closure},
            f"|interaction| > {args.interaction_share:g} x |total|, or closure outside [0.5, 1.5]",
            "one run per case; the statistical closure assumes independent prior parameters"
            + (
                "; FIELD parameters are not in the statistical estimate, so its closure is not checked"
                if ctx["fields"]
                else ""
            ),
            nonlinear + bad_closure,
        )
    )
    outside_prior = [r["name"] for r in rows if r["truth_in_prior_range"] is False]
    out.append(
        check(
            "HMQC-PARAM-TRUTH-PRIOR",
            outside_prior,
            f"{len(outside_prior)} parameter truths are outside the prior ensemble min-max",
            {"count": len(outside_prior), "with_truth": sum(1 for r in rows if r["truth"] is not None)},
            "truth outside prior ensemble min-max",
            "synthetic cases only" if any(r["truth"] is not None for r in rows) else "no truth given",
            outside_prior,
        )
    )
    return out


def is_below(value, limit: float) -> bool:
    return value is not None and value < limit


def fmt_pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def fmt(value, spec: str = ".3g") -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return format(value, spec)


def headline(ctx: dict) -> list[str]:
    fam = {g["group"]: g for g in ctx["groups"] if g["level"] == "family"}
    prior = ctx["manifest"].get("misfit_vs_chi2", {})
    lines = [
        f"N = {ctx['influence']['n']}, {len(ctx['parameters'])} uncertain parameters, {len(ctx['obs'])} observations",
        f"posterior trust: {ctx['posterior_trust']}",
    ]
    for label in (ctx["prior_label"], ctx["posterior_label"]):
        if label in prior:
            lines.append(
                f"{label} mean misfit {fmt(prior[label].get('mean_misfit'))} ({prior[label].get('verdict')})"
            )
    lines.append(
        f"prior-predictive coverage {fmt_pct(ctx['observations']['prior_predictive']['covered_share'])}"
    )
    lines.append(f"{len(fam)} observation families")
    return lines


def markdown(doc: dict, ctx: dict) -> str:
    lines = [
        "# AHM overview",
        "",
        "; ".join(doc["headline"]),
        "",
        doc["conventions"]["percentiles"],
        "",
        "## Checks that fired",
        "",
        "| id | severity | headline |",
        "|---|---|---|",
    ]
    fired: dict[str, list[dict]] = {}
    for c in sorted(
        (c for c in doc["checks"] if c["fired"]),
        key=lambda c: (c["severity"] != "warning", c["source"] != "ahm_overview"),
    ):
        fired.setdefault(c["id"], []).append(c)
    for cid, items in list(fired.items())[:MD_CHECKS]:
        more = f" (and {len(items) - 1} more)" if len(items) > 1 else ""
        lines.append(f"| {cid} | {items[0]['severity']} | {items[0]['headline'].replace('|', '/')}{more} |")
    if len(fired) > MD_CHECKS:
        lines.append(f"| ... | | {len(fired) - MD_CHECKS} more check ids in overview.json |")
    lines += ["", "## Misfit by family (prior -> posterior, chi2 band)", ""]
    for g in (g for g in doc["observations"]["groups"] if g["level"] == "family"):
        band = g["chi2_band"] or [None, None]
        lines.append(
            f"- {g['group']}: {fmt(g['prior_misfit'])} -> {fmt(g['post_misfit'])} (n={g['n_obs']},"
            f" band {fmt(band[0])}-{fmt(band[1])}, prior coverage {fmt_pct(g['pp_covered_share'])})"
        )
    lines += [
        "",
        "## Parameters by |shift| (top 10)",
        "",
        "| name | class | shift | sd ratio | truth z |",
        "|---|---|---|---|---|",
    ]
    ranked = sorted(doc["parameters"], key=lambda r: (-abs(r["shift"] or 0), r["name"]))
    for r in ranked[:10]:
        lines.append(
            f"| {r['name']} | {r['class']}/{r['category']} | {fmt(r['shift'], '.2f')} | {fmt(r['sd_ratio'], '.2f')}"
            f" | {fmt(r['truth_z_post'], '.2f')} |"
        )
    sd_ = doc["static_dynamic"]
    lines += [
        "",
        "Static vs dynamic share of |shift|: "
        + ", ".join(
            f"{k} {fmt_pct(sd_[k]['share_of_update'])} ({sd_[k]['n_params']})"
            for k in ("static", "dynamic", "unclassified")
        ),
        "",
        "## Drivers (top 5 families by update share, linear estimate)",
        "",
    ]
    fam_inf = [g for g in doc["influence"]["groups"] if g["level"] == "family"]
    for g in sorted(fam_inf, key=lambda g: (-(g["update_share"] or 0), g["group"]))[:5]:
        lines.append(f"- {g['group']}: update {fmt_pct(g['update_share'])}, DFS {fmt_pct(g['dfs_share'])}")
    lines += ["", "## Conflicts (top 3)", ""]
    for c in doc["conflicts"]["pairs"][:3]:
        names = ", ".join(p["name"] for p in c["params"])
        lines.append(f"- {c['level']} {c['a']} vs {c['b']}: cosine {c['cosine']:.2f} ({names})")
    if not doc["conflicts"]["pairs"]:
        lines.append("- none")
    lines += ["", "## Noise by family (posterior misfit, verdict, truth-run misfit)", ""]
    truth = {f["family"]: f for f in (doc["noise"]["truth_run"] or {}).get("families", [])}
    for g in (g for g in doc["noise"]["groups"] if g["level"] == "family"):
        t = truth.get(g["group"])
        lines.append(
            f"- {g['group']}: {fmt(g['post_misfit'])}, {g['verdict']}, truth {fmt(t['misfit']) if t else '-'}"
        )
    lines += ["", "## Forecast", ""]
    for f in doc["forecast"]["items"][:5]:
        sa = f["statistical_attribution"]
        lines.append(
            f"- {f['id']}: mean {fmt(f['prior']['mean'])} -> {fmt(f['posterior']['mean'])}, sd ratio"
            f" {fmt(f['sd_ratio'], '.2f')}, truth {fmt(f['truth'])}, closure {fmt(sa['closure'], '.2f')}"
        )
    if not doc["forecast"]["items"]:
        lines.append("- not given")
    lines += ["", "## Attribution (runs)", ""]
    for a in doc["attribution"]["keys"][:5]:
        lines.append(
            f"- {a['key']}: total {fmt(a['total'])}, static {fmt(a['static_effect'])}, dynamic"
            f" {fmt(a['dynamic_effect'])}, interaction {fmt(a['interaction'])}"
        )
    if not doc["attribution"]["keys"]:
        lines.append("- not given")
    lines += ["", "## Not checked / limits", "", "- " + "; ".join(doc["limits"])]
    lines += ["", f"Reference: {REFERENCE}", ""]
    return "\n".join(line for i, line in enumerate(lines) if line or not lines[i - 1].startswith("## "))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    f: json.dumps(v) if isinstance(v, list | dict) else v
                    for f, v in clean(row).items()
                    if f in fields
                }
            )


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--misfit-dir", type=Path, required=True)
    parser.add_argument("--dist", type=Path, action="append", required=True)
    parser.add_argument("--prior-runpaths")
    parser.add_argument("--posterior-runpaths")
    parser.add_argument("--prior-params-csv", type=Path)
    parser.add_argument("--posterior-params-csv", type=Path)
    parser.add_argument("--prior-label", default="prior")
    parser.add_argument("--posterior-label", default="posterior")
    parser.add_argument("--truth", type=Path)
    parser.add_argument("--truth-misfit-dir", type=Path)
    parser.add_argument("--field-qc", type=Path, action="append", default=[])
    parser.add_argument("--forecast-prior", type=Path)
    parser.add_argument("--forecast-posterior", type=Path)
    parser.add_argument("--forecast-truth", action="append", default=[])
    parser.add_argument("--attribution-csv", type=Path)
    parser.add_argument("--write-attribution", type=Path)
    parser.add_argument("--param-class", action="append", default=[])
    parser.add_argument("--outdir", type=Path)
    for name, default in (
        ("collapse-ratio", 0.2),
        ("truth-z", 3.0),
        ("pp-z", 3.0),
        ("pp-share", 0.9),
        ("pp-group-share", 0.7),
        ("outlier-z", 3.0),
        ("dominant-share", 0.5),
        ("conflict-min-share", 0.1),
        ("conflict-cos", -0.5),
        ("spurious-shift", 1.0),
        ("spurious-linear", 0.25),
        ("spurious-subshare", 0.5),
        ("autocorr-limit", 0.5),
        ("interaction-share", 0.3),
    ):
        parser.add_argument(f"--{name}", type=float, default=default)
    args = parser.parse_args(argv)
    runpaths = args.prior_runpaths is not None, args.posterior_runpaths is not None
    tables = args.prior_params_csv is not None, args.posterior_params_csv is not None
    if not ((all(runpaths) and not any(tables)) or (all(tables) and not any(runpaths))):
        parser.error(
            "give --prior-runpaths and --posterior-runpaths, or --prior-params-csv and --posterior-params-csv"
        )
    if (args.forecast_prior is None) != (args.forecast_posterior is None):
        parser.error("--forecast-prior and --forecast-posterior go together")
    if args.forecast_truth and args.forecast_prior is None:
        parser.error("--forecast-truth needs the forecast tables")
    if args.outdir is None and args.write_attribution is None:
        parser.error("--outdir is required")
    try:
        args.rules = [parse_class_rule(text) for text in args.param_class] + list(DEFAULT_CLASSES)
    except (ValueError, re.error) as error:
        parser.error(str(error))
    return args


def load(args) -> dict:
    manifest = read_manifest(args.misfit_dir)
    require_complete(manifest)
    reals = {label: realizations_used(manifest, label) for label in (args.prior_label, args.posterior_label)}
    params = read_dists(args.dist)
    if args.prior_params_csv:
        prior_native = read_params_csv(args.prior_params_csv)
        post_native = read_params_csv(args.posterior_params_csv)
    else:
        prior_native = read_params_runpaths(args.prior_runpaths, reals[args.prior_label])
        post_native = read_params_runpaths(args.posterior_runpaths, reals[args.posterior_label])
    check_reals(prior_native, reals[args.prior_label], "prior parameters")
    check_reals(post_native, reals[args.posterior_label], "posterior parameters")
    prior_names = full_names(prior_native, params, "prior parameters")
    post_names = full_names(post_native, params, "posterior parameters")
    return {
        "manifest": manifest,
        "reals": reals,
        "params": params,
        "prior_native": prior_native,
        "post_native": post_native,
        "prior_names": prior_names,
        "post_names": post_names,
        "prior_s": scaled(prior_native, params, prior_names),
        "post_s": scaled(post_native, params, post_names),
    }


def analyse(args, data: dict) -> dict:
    manifest, params = data["manifest"], data["params"]
    prior_s, post_s = data["prior_s"], data["post_s"]
    res = read_residuals(args.misfit_dir)
    res = res[res["ENSEMBLE"].isin([args.prior_label, args.posterior_label])]
    for label in (args.prior_label, args.posterior_label):
        found = sorted(int(r) for r in res.loc[res["ENSEMBLE"] == label, "REAL"].unique())
        if found != data["reals"][label]:
            raise InputError(
                f"residuals.csv {label}: realizations differ from realizations_used in the manifest"
            )
    truth = prior_qc.read_truth(args.truth) if args.truth else None
    rules = args.rules
    rows = parameter_rows(
        params,
        prior_s,
        post_s,
        data["post_native"].rename(columns={v: k for k, v in data["post_names"].items()}),
        truth,
        rules,
    )
    obs = obs_table(res, args.prior_label, args.posterior_label, args.pp_z)
    groups = observation_groups(res, obs, args.prior_label, args.posterior_label)
    inf = influence(prior_s, res, obs, args.prior_label)
    per_param = parameter_influence(inf, obs)
    for r in rows:
        if r["name"] in per_param:
            p = per_param[r["name"]]
            r.update(
                linear_update=p["linear_update"],
                subthreshold_share=p["subthreshold_share"],
                significant_obs=p["significant_obs"],
                driver_group=p["driver_group"],
                driver_share=p["driver_share"],
            )
    fields = [read_field_qc(path, rules) for path in args.field_qc]
    classes = {r["name"]: (r["class"], r["category"]) for r in rows}
    fc = []
    if args.forecast_prior:
        prior_f = read_forecast(args.forecast_prior, data["reals"][args.prior_label])
        post_f = read_forecast(args.forecast_posterior, data["reals"][args.posterior_label])
        require_same_forecast_ids(prior_f, post_f)
        ftruth = parse_forecast_truth(args.forecast_truth, sorted(prior_f))
        fc = forecast(prior_f, post_f, ftruth, prior_s, post_s, classes)
    truth_res = read_residuals(args.truth_misfit_dir) if args.truth_misfit_dir else None
    gate = collapse_checks.trust_gate(len(post_s), len(rows))
    trust = manifest.get("trust", {})
    ctx = {
        "manifest": manifest,
        "prior_label": args.prior_label,
        "posterior_label": args.posterior_label,
        "parameters": rows,
        "obs": obs,
        "groups": groups,
        "observations": obs_lists(obs, args),
        "influence": inf,
        "groups_influence": group_influence(inf, obs),
        "per_param": per_param,
        "closure": closure(rows, args),
        "conflicts": pair_conflicts(inf, obs, args),
        "tug_of_war": [
            {"name": n, "push_up": p["push_up"], "push_down": p["push_down"], "tug": p["tug"]}
            for n, p in sorted(per_param.items())
            if p["tug"] >= 0.5 and p["push_up"] >= 0.5 and p["push_down"] >= 0.5
        ],
        "noise": noise(groups, obs, truth_res, args),
        "fields": fields,
        "forecast": fc,
        "attribution": read_attribution(args.attribution_csv) if args.attribution_csv else [],
        "static_dynamic": static_dynamic(rows, fields, args),
        "trust": trust,
        "computed_trust": gate,
        "posterior_trust": trust.get(args.posterior_label, gate).get("level"),
    }
    prior_found = prior_qc_findings(
        params,
        data["prior_native"],
        data["post_native"],
        data["prior_names"],
        data["post_names"],
        truth,
        args,
    )
    ctx["checks"] = new_checks(ctx, args) + existing_checks(manifest, prior_found, fields)
    return ctx


def document(args, ctx: dict, data: dict) -> dict:
    inf = ctx["influence"]
    thresholds = {
        k: getattr(args, k)
        for k in (
            "collapse_ratio",
            "truth_z",
            "pp_z",
            "pp_share",
            "pp_group_share",
            "outlier_z",
            "dominant_share",
            "conflict_min_share",
            "conflict_cos",
            "spurious_shift",
            "spurious_linear",
            "spurious_subshare",
            "autocorr_limit",
            "interaction_share",
        )
    }
    observations = {"groups": ctx["groups"], **ctx["observations"]}
    obs_rows = [
        {
            "obs": o,
            "key": ctx["obs"].loc[o, "KEY"],
            "family": ctx["obs"].loc[o, "family"],
            "s_j": float(inf["s"][j]),
            "e_j": float(inf["e"][j]),
            "w_norm": float(np.linalg.norm(inf["w"][:, j])),
        }
        for j, o in enumerate(inf["obs"])
    ]
    doc = {
        "schema_version": SCHEMA,
        "conventions": {
            "percentiles": P10_P90,
            "percentile_method": "linear interpolation (Hyndman-Fan type 7)",
            "parameter_scale": "sampling scale: log10 for LOGUNIF and LOGNORMAL, native otherwise",
            "prior_sd": "prior ensemble sd in the sampling scale (ddof=1); prior_sd_dist from the distribution",
            "shift": "(posterior mean - prior mean) / prior sd",
            "sd_ratio": "posterior sd / prior sd",
            "residual": "(simulated - observed) / error, as misfit_report.py",
            "misfit": "mean of squared normalized residuals over finite rows",
            "z_pp": "(observed - prior mean simulated) / sqrt(prior sd simulated^2 + error^2)",
            "influence": "K = C_md (C_dd + I)^-1 on standardized prior parameters and error-scaled responses;"
            " W_ij = K_ij e_j in prior sd; update_share = group norm / sum of group norms at the level",
            "rounding": "floats to 6 significant digits; lists sorted by level then group or by name unless a"
            " list states sorted_by",
        },
        "references": [REFERENCE, *sorted(RATIONALE)],
        "inputs": {
            "misfit_dir": str(args.misfit_dir),
            "dist": [str(p) for p in args.dist],
            "prior": args.prior_runpaths or str(args.prior_params_csv),
            "posterior": args.posterior_runpaths or str(args.posterior_params_csv),
            "truth": str(args.truth) if args.truth else None,
            "truth_misfit_dir": str(args.truth_misfit_dir) if args.truth_misfit_dir else None,
            "field_qc": [str(p) for p in args.field_qc],
            "forecast_prior": str(args.forecast_prior) if args.forecast_prior else None,
            "forecast_posterior": str(args.forecast_posterior) if args.forecast_posterior else None,
            "attribution_csv": str(args.attribution_csv) if args.attribution_csv else None,
            "param_class": args.param_class,
            "thresholds": thresholds,
        },
        "ensembles": {
            label: {"realizations": reals, "n": len(reals)} for label, reals in data["reals"].items()
        },
        "trust": {"manifest": ctx["trust"], "computed_posterior": ctx["computed_trust"]},
        "headline": headline(ctx),
        "checks": ctx["checks"],
        "parameters": ctx["parameters"],
        "static_dynamic": ctx["static_dynamic"],
        "fields": ctx["fields"],
        "observations": observations,
        "influence": {
            "n": inf["n"],
            "p": inf["p"],
            "m": inf["m"],
            "rank_d": inf["rank"],
            "rank_deficient": inf["n"] <= inf["p"],
            "dropped_params": inf["dropped_params"],
            "excluded_obs": inf["excluded_obs"],
            "dfs": float(inf["s"].sum()),
            "groups": ctx["groups_influence"],
            "parameters": ctx["per_param"],
            "closure": ctx["closure"],
            "method": "linear-Gaussian, one ES step from the prior ensemble, all observations, error covariance I",
        },
        "conflicts": {
            "pairs": ctx["conflicts"],
            "tug_of_war": ctx["tug_of_war"],
            "sorted_by": "cosine ascending",
        },
        "noise": ctx["noise"],
        "forecast": {"items": ctx["forecast"], "conventions": P10_P90},
        "attribution": {"keys": ctx["attribution"], "method": "run-based: base, static, dynamic, all"},
        "limits": [
            "Localization, ES-MDA inflation and iterations are not modelled in the influence estimate",
            "The influence estimate is linear and uses the prior ensemble only",
            "With N <= p the estimate is rank deficient"
            if inf["n"] <= inf["p"]
            else "N > p: the estimate has full rank",
            "Fields are ingested from field_qc.py; they are not in the influence estimate",
            "Observation errors are taken as independent (error covariance I)",
            "Not checked: deck, SCAL, PVT, and the ERT config",
        ],
    }
    doc["_obs_rows"] = obs_rows
    return doc


def write_outputs(outdir: Path, doc: dict, ctx: dict) -> None:
    obs_rows = doc.pop("_obs_rows")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "overview.json").write_text(json.dumps(clean(doc), indent=2, sort_keys=True) + "\n")
    (outdir / "overview.md").write_text(markdown(clean(doc), ctx))
    write_csv(
        outdir / "parameters.csv",
        doc["parameters"],
        list(doc["parameters"][0]) if doc["parameters"] else ["name"],
    )
    influence_by_group = {(i["level"], i["group"]): i for i in doc["influence"]["groups"]}
    group_rows = [
        {**influence_by_group.get((g["level"], g["group"]), {}), **g} for g in doc["observations"]["groups"]
    ]
    fields = [
        "level",
        "group",
        "n_obs",
        "prior_misfit",
        "post_misfit",
        "misfit_change_ratio",
        "chi2_band",
        "pp_covered_share",
        "dfs",
        "dfs_share",
        "update_norm",
        "update_share",
        "top_params",
    ]
    write_csv(outdir / "observation_groups.csv", group_rows, fields)
    write_csv(
        outdir / "observations_influence.csv", obs_rows, ["obs", "key", "family", "s_j", "e_j", "w_norm"]
    )
    write_csv(outdir / "conflicts.csv", doc["conflicts"]["pairs"], ["level", "a", "b", "cosine", "params"])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        data = load(args)
        if args.write_attribution:
            write_attribution(
                args.write_attribution,
                data["params"],
                data["prior_s"],
                data["post_s"],
                data["prior_names"],
                data["prior_native"],
                args.rules,
            )
            print(f"Wrote {args.write_attribution}")
            return 0
        ctx = analyse(args, data)
        doc = document(args, ctx, data)
    except (InputError, OSError, ValueError, KeyError, json.JSONDecodeError, pd.errors.ParserError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_outputs(args.outdir, doc, ctx)
    for c in doc["checks"]:
        if c["fired"]:
            print(f"CHECK {c['id']} {c['severity']}: {c['headline']}")
    print(f"Wrote {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
