#!/usr/bin/env python3
"""Quality control of a history match from the files in the runpaths. Read-only.

It compares simulated summary vectors with summary observations, and GEN_DATA results with
GENERAL_OBSERVATION blocks (for example RFT pressures). It does this for one ensemble
(for example the posterior) or for two (prior and posterior). It also compares the parameters.

This is a diagnostic. It is not the ERT update step. It does not use the ERT storage.
A rate or ratio vector (for example WOPR, WWCT, WGOR) takes the value at the end of the time step that holds
the observation date, because the summary file stores the mean over each step. Other vectors (cumulatives,
pressures) use linear interpolation in time. A value at a date outside the simulated period is not used.

Usage:
    misfit_report.py --obs <file.obs> [--obs <file2.obs>] \
        --posterior '<runpath glob>' [--prior '<runpath glob>'] \
        --expected 100 --outdir <dir>
    Use --realizations 0-4,7 instead of --expected when the run used a subset.
    --eclbase 'model/DROGON-<IENS>' takes the ERT ECLBASE; <IENS> is the realization number and the path is
    relative to the runpath. A plain deck name, or no --eclbase, searches eclipse/model, then model, then the
    runpath. More than one .DATA file in the first folder with a match is an issue of that member.
    --ert-config <file.ert> reads the GEN_DATA RESULT_FILE lines; --gen-data NAME=RESULT_FILE (repeatable)
    adds or overrides one. %d in a result file is the RESTART step of the observation; the path is relative
    to the runpath. Without both, GENERAL_OBSERVATION blocks are not used and a NOTE names them.
    A missing GEN_DATA file or index is a missing prediction, as a missing summary value is.

Outputs in --outdir:
    residuals.csv       one row for each ensemble, realization and observation
    obs_summary.csv     for each ensemble and observation: mean, spread, envelope, residual, predictive CDF
                        (sim + N(0, error)) and INSIDE_PREDICTIVE_P5_P95, blank below two members. manifest.json
                        coverage_p5_p95 has the predictive coverage per ensemble (coverage.py) on the complete
                        configured-observation x expected-member grid, or an error entry when that grid is
                        incomplete. Quote it, not INSIDE_ENVELOPE.
    real_misfit.csv     for each ensemble and realization: mean squared normalized residual
    params_shift.csv    parameter change from prior to posterior (with --prior)
    manifest.json       inputs, counts, versions, findings

GEN observations are named <OBS>:<index> with KEY GEN:<DATA>. manifest.json misfit_by_family groups
the observations by vector mnemonic (WOPR, WBHP) and puts all GEN observations in the family GEN.

Normalized residual = (simulated - observed) / error. The error follows the ERT rules.
Misfit of a realization = mean of the squared normalized residuals. This is not the ERT objective function.
Native vector units must be available and identical across members and ensembles. A blank unit is valid
only for a water cut vector (a fraction); the report records it as "". Observation values
and errors are assumed to use those units; the observation format has no unit field. No conversion is made.
Nonfinite inputs or derived statistics are rejected before any report is written, even with --allow-partial.
Blank statistics are allowed only for missing predictions, a predictive CDF below two members, or undefined
ratios for a constant prior parameter.
Members without usable predictions remain listed with OBS_USED=0 and a blank MISFIT; partial reports exit 1.
Findings are hints. They do not change the exit code.
Exit code: 0 = done, 1 = input problem (missing members, bad observations, no usable observation), 2 = usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import numpy as np
import pandas as pd
import runpath_lib
from res2df import ResdataFiles as SimFiles
from res2df import summary as summary_module

HERE = Path(__file__).resolve().parent
DESIGN_LABELS = {"SENSNAME", "SENSCASE"}
REACH_TAIL = 0.005  # out of reach: the predictive CDF at the observation is below this or above 1 minus this


def _load_sibling(name: str, folder: Path = HERE):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


obs_qc = _load_sibling("obs_qc")
collapse_checks = _load_sibling("collapse_checks")
coverage_lib = _load_sibling("coverage")
update_log_report = _load_sibling("update_log_report")
ert_config_map = _load_sibling("ert_config_map", HERE.parents[1] / "ert-config-author-lint" / "scripts")


class IntegrityError(ValueError):
    """Invalid numeric evidence or native units; --allow-partial cannot waive this."""


def require_finite(values, context: str) -> None:
    try:
        finite = np.isfinite(np.asarray(values, dtype=float)).all()
    except (ValueError, TypeError, OverflowError) as error:
        raise IntegrityError(f"{context} must be finite numeric values") from error
    if not finite:
        raise IntegrityError(f"{context} must be finite (numeric overflow or invalid value)")


def validate_numeric(table: pd.DataFrame, context: str, nullable: dict | None = None) -> None:
    """Only explicitly undefined cells may be null; infinity is never valid."""
    for column in table.select_dtypes(include="number"):
        values = table[column].to_numpy(dtype=float)
        allowed = (nullable or {}).get(column, np.zeros(len(table), dtype=bool))
        if not (np.isfinite(values) | (np.isnan(values) & np.asarray(allowed))).all():
            raise IntegrityError(f"{context}.{column} must be finite except explicitly undefined cells")


def read_parameters(runpath: Path) -> dict[str, float]:
    params: dict[str, float] = {}
    file = runpath / "parameters.txt"
    if file.is_file():
        try:
            lines = file.read_text().splitlines()
        except (OSError, UnicodeError) as error:
            raise IntegrityError(f"parameters {file}: cannot read: {error}") from error
        for number, line in enumerate(lines, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) == 2 and parts[0] in DESIGN_LABELS:
                continue  # DESIGN_MATRIX (fmudesign) writes the sensitivity name and case as text
            if len(parts) != 2 or parts[0] in params:
                raise IntegrityError(f"parameters {file}:{number}: expected one unique name and value")
            try:
                value = float(parts[1])
            except ValueError as error:
                raise IntegrityError(f"parameters {file}:{number}: invalid numeric value") from error
            require_finite(value, f"parameters {file}:{number}")
            params[parts[0]] = value
    return {k: v for k, v in params.items() if not (k.startswith("LOG10_") and k[len("LOG10_") :] in params)}


# Same pattern and function in ensemble_summary.py and misfit_report.py.
# tests/test_dimensionless_units.py checks that the copies agree.
# Water cut is a fraction. The simulator writes a blank unit for it, and that is correct.
# A blank unit for any other vector means that the unit is unavailable.
DIMENSIONLESS_MNEMONIC = re.compile(r"^[FGWRC]WCTH?$")


def unit_is_valid(key: str, unit) -> bool:
    """A unit must be a string. Only a dimensionless vector can have a blank unit."""
    if not isinstance(unit, str):
        return False
    return bool(unit.strip()) or bool(DIMENSIONLESS_MNEMONIC.match(key.split(":", 1)[0]))


def read_units(source, keys: list[str]) -> dict[str, str]:
    """Use the installed adapter's native summary API, never guessed units."""
    try:
        getter = getattr(source, "get_summary", None) or getattr(source, "get_eclsum", None)
        if getter is None:
            raise ValueError("summary reader has no native unit API")
        summary = getter()
        units = {key: summary.unit(key) for key in keys}
        if not all(unit_is_valid(key, unit) for key, unit in units.items()):
            raise ValueError("requested vector unit is unavailable")
        return {key: unit.strip() for key, unit in units.items()}
    except Exception as error:
        raise IntegrityError(f"native summary units unavailable: {error}") from error


# A rate or a ratio in a summary file is the mean over the time step that ends at the report time.
# So the value for a date inside a step is the value at the end of that step. Linear interpolation is wrong.
# Cumulative vectors, pressures, and other vectors are values at a point in time. They use linear interpolation.
STEP_MNEMONIC = re.compile(r"^[FGWRC]([OWGLV][PI]R|WCT|GOR|GLR|OGR|WGR)H?$")


def time_rule(key: str) -> str:
    """Return 'step' for rate and ratio vectors, 'linear' for the other vectors."""
    return "step" if STEP_MNEMONIC.match(key.split(":", 1)[0]) else "linear"


def simulated_at(frame: pd.DataFrame, key: str, when: datetime) -> tuple[float | None, str]:
    """Return (value, status). Status is exact, interpolated, step, outside, or missing_key."""
    if key not in frame.columns:
        return None, "missing_key"
    series = frame[key].dropna()
    if series.empty:
        return None, "missing_key"
    stamp = pd.Timestamp(when)
    if stamp < series.index.min() or stamp > series.index.max():
        return None, "outside"
    if stamp in series.index:
        value = series.loc[stamp]
        return float(value.iloc[-1] if hasattr(value, "iloc") else value), "exact"
    if time_rule(key) == "step":
        return float(series[series.index > stamp].iloc[0]), "step"
    # Elapsed seconds, not raw int64 ticks: the index resolution (s, ms, us, ns) depends on the pandas version.
    start = series.index[0]
    seconds = (series.index - start).total_seconds()
    elapsed = (stamp - start).total_seconds()
    return float(np.interp(elapsed, seconds, series.to_numpy(dtype=float))), "interpolated"


def gen_data_sources(pairs: list[str], ert_config: Path | None) -> dict[str, str]:
    """RESULT_FILE of each GEN_DATA response, from the ERT config and then --gen-data NAME=RESULT_FILE."""
    files: dict[str, str] = {}
    if ert_config is not None:
        mapper = ert_config_map.Mapper(ert_config)
        mapper.parse(mapper.root)
        broken = [i["target"] for i in mapper.includes if i["status"] != "ok"]
        if broken or mapper.problems:
            raise ValueError(f"cannot read all of {ert_config}: {broken or mapper.problems}")
        for item in mapper.parameters:
            if item["keyword"] == "GEN_DATA":
                for option in item["options"]:
                    name, _, value = option.partition(":")
                    if name == "RESULT_FILE" and value:
                        files[item["name"]] = value
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not (sep and name.strip() and value.strip()):
            raise ValueError(f"--gen-data needs NAME=RESULT_FILE, not '{pair}'")
        files[name.strip()] = value.strip()
    return files


def general_points(obs: dict, result_file: str) -> list[dict]:
    """One observation for each data point of a GENERAL_OBSERVATION."""
    return [
        {
            "name": f"{obs['name']}:{index}",
            "key": f"GEN:{obs['data']}",
            "date": None,
            "value": value,
            "error": error,
            "data": obs["data"],
            "index": index,
            "report_step": obs["report_step"],
            "result_file": result_file,
        }
        for index, value, error in zip(obs["indices"], obs["values"], obs["errors"], strict=True)
    ]


def read_gen_data(
    runpath: Path, files: dict[tuple[str, int], str]
) -> dict[tuple[str, int], list[float] | None]:
    """Values of each (GEN_DATA, report step) of one member. A missing file gives None."""
    out: dict[tuple[str, int], list[float] | None] = {}
    for (data, step), template in files.items():
        file = runpath / template.replace("%d", str(step))
        if not file.is_file():
            out[(data, step)] = None
            continue
        try:
            values = [float(token) for token in file.read_text().split()]
        except (OSError, UnicodeError, ValueError) as error:
            raise IntegrityError(f"GEN_DATA {file}: cannot read numbers: {error}") from error
        require_finite(values, f"GEN_DATA {file}")
        out[(data, step)] = values
    return out


def generated_at(gen: dict, obs: dict) -> tuple[float | None, str]:
    """Return (value, status). Status is exact, missing_file, or missing_index."""
    values = gen.get((obs["data"], obs["report_step"]))
    if values is None:
        return None, "missing_file"
    if obs["index"] >= len(values):
        return None, "missing_index"
    return values[obs["index"]], "exact"


MISSING_STATUS = ["outside", "missing_key", "missing_file", "missing_index"]


def load_ensemble(
    label: str,
    pattern: str,
    keys: list[str],
    eclbase: str | None,
    expected: set[int] | None,
    gen_files: dict[tuple[str, int], str] | None = None,
):
    ensemble = runpath_lib.read_ensemble(pattern, eclbase, expected)
    extra = sorted(drop.realization for drop in ensemble.extra)
    frames: dict[int, pd.DataFrame] = {}
    params: dict[int, dict[str, float]] = {}
    issues: list[dict] = [
        {"ensemble": label, "realization": drop.realization, "issue": drop.reason}
        for drop in ensemble.dropped
    ]
    seen = set()
    reference_units = None
    for member in ensemble.found:
        real, runpath, file = member.realization, member.runpath, member.data_file
        assert file is not None
        if real in seen:
            issues.append({"ensemble": label, "realization": real, "issue": "duplicate realization"})
            frames.pop(real, None)
            params.pop(real, None)
            continue
        seen.add(real)
        if not file.with_suffix(".UNSMRY").is_file():
            issues.append({"ensemble": label, "realization": real, "issue": f"missing {file.stem}.UNSMRY"})
            continue
        try:
            parameters = read_parameters(runpath)
            source = SimFiles(str(file))
            native_units = read_units(source, keys)
            if reference_units is not None and native_units != reference_units:
                raise IntegrityError(f"native vector units differ: {native_units} vs {reference_units}")
            reference_units = native_units
            frame = summary_module.df(source, time_index="raw", column_keys=keys)
            require_finite(frame.to_numpy(), "summary values")
            if frame.empty or not set(keys) <= set(frame.columns):
                raise ValueError("requested observation vectors are missing")
            if (
                not isinstance(frame.index, pd.DatetimeIndex)
                or frame.index.hasnans
                or not frame.index.is_unique
                or not frame.index.is_monotonic_increasing
            ):
                raise ValueError("summary dates must be valid, unique and increasing")
            frame.attrs["units"] = native_units
            frame.attrs["gen"] = read_gen_data(runpath, gen_files or {})
            frames[real] = frame
            params[real] = parameters
        except Exception as error:  # report each failure with its cause
            issues.append(
                {
                    "ensemble": label,
                    "realization": real,
                    "issue": f"{type(error).__name__}: {error}",
                    "fatal": isinstance(error, IntegrityError),
                }
            )
            continue

    missing = sorted(expected - set(frames)) if expected is not None else []
    return frames, params, issues, missing, extra


def residual_table(label: str, frames: dict[int, pd.DataFrame], observations: list[dict]) -> pd.DataFrame:
    rows = []
    for real, frame in frames.items():
        for obs in observations:
            if "data" in obs:
                value, status = generated_at(frame.attrs.get("gen", {}), obs)
            else:
                value, status = simulated_at(frame, obs["key"], datetime.fromisoformat(obs["date"]))
            rows.append(
                {
                    "ENSEMBLE": label,
                    "REAL": real,
                    "OBS": obs["name"],
                    "KEY": obs["key"],
                    "DATE": obs["date"],
                    "OBSERVED": obs["value"],
                    "ERROR": obs["error"],
                    "SIMULATED": value,
                    "STATUS": status,
                    "RESIDUAL": None if value is None else (value - obs["value"]) / obs["error"],
                }
            )
    return pd.DataFrame(rows).astype({"SIMULATED": float, "RESIDUAL": float})


def family_misfit(table: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """For each ensemble and family (the vector mnemonic, or GEN): observations and mean squared residual."""
    used = table.dropna(subset=["RESIDUAL"])
    used = used.assign(FAMILY=used["KEY"].str.split(":", n=1).str[0], SQ=used["RESIDUAL"] ** 2)
    out: dict[str, dict[str, dict]] = {}
    for (label, family), group in used.groupby(["ENSEMBLE", "FAMILY"], sort=True):
        out.setdefault(label, {})[family] = {
            "observations": int(group["OBS"].nunique()),
            "mean_squared_residual": float(group["SQ"].mean()),
        }
    return out


def coverage_block(
    table: pd.DataFrame,
    labels: list[str],
    expected_obs: set[str] | None = None,
    expected_members: dict[str, set[int]] | None = None,
) -> dict:
    """Predictive coverage per ensemble (coverage.py) on the complete inventory. The noise-biased share is kept, labelled."""
    out = {}
    for label in labels:
        members = None if expected_members is None else expected_members[label]
        try:
            result = coverage_lib.from_residuals(
                table, label, expected_obs=expected_obs, expected_members=members
            )
        except ValueError as error:
            out[label] = {"error": str(error)}
            continue
        out[label] = {
            "predictive": result.predictive,
            "noise_biased_do_not_quote": result.noise_biased,
            "nominal": result.nominal,
            "observations": result.n_obs,
            "findings": result.findings,
        }
    return out


def obs_summary(table: pd.DataFrame) -> pd.DataFrame:
    used = table.dropna(subset=["SIMULATED"])
    rows = []
    for (label, name), group in table.groupby(["ENSEMBLE", "OBS"], sort=False):
        valid = used[(used["ENSEMBLE"] == label) & (used["OBS"] == name)]
        head = group.iloc[0]
        row = {
            "ENSEMBLE": label,
            "OBS": name,
            "KEY": head["KEY"],
            "DATE": head["DATE"],
            "OBSERVED": head["OBSERVED"],
            "ERROR": head["ERROR"],
            "COUNT": len(valid),
            "STATUS": ",".join(sorted(group["STATUS"].unique())),
        }
        if len(valid):
            sim = valid["SIMULATED"]
            row.update(
                SIM_MEAN=sim.mean(),
                SIM_STD=sim.std(ddof=0),
                SIM_MIN=sim.min(),
                SIM_MAX=sim.max(),
                MEAN_RESIDUAL=valid["RESIDUAL"].mean(),
                SPREAD_RATIO=sim.std(ddof=0) / head["ERROR"],
                INSIDE_ENVELOPE=bool(sim.min() <= head["OBSERVED"] <= sim.max()),
            )
        if len(valid) >= 2:
            cdf = float(
                coverage_lib.predictive_cdf(
                    sim.to_numpy()[:, None], np.array([head["OBSERVED"]]), np.array([head["ERROR"]])
                )[0]
            )
            row.update(
                PREDICTIVE_CDF=cdf,
                INSIDE_PREDICTIVE_P5_P95=bool(coverage_lib.LOW <= cdf <= coverage_lib.HIGH),
            )
        rows.append(row)
    return pd.DataFrame(rows)


def real_misfit(table: pd.DataFrame) -> pd.DataFrame:
    used = table.dropna(subset=["RESIDUAL"]).copy()
    used["SQ"] = used["RESIDUAL"] ** 2
    out = used.groupby(["ENSEMBLE", "REAL"]).agg(OBS_USED=("SQ", "size"), MISFIT=("SQ", "mean")).reset_index()
    out = table[["ENSEMBLE", "REAL"]].drop_duplicates().merge(out, how="left", on=["ENSEMBLE", "REAL"])
    out["OBS_USED"] = out["OBS_USED"].fillna(0).astype(int)
    return out


def parameter_shift(
    prior: dict[int, dict[str, float]], posterior: dict[int, dict[str, float]]
) -> pd.DataFrame:
    names = sorted(
        set().union(*(p.keys() for p in prior.values()))
        & set().union(*(p.keys() for p in posterior.values()))
    )
    rows = []
    for name in names:
        a = np.array([p[name] for p in prior.values() if name in p], dtype=float)
        b = np.array([p[name] for p in posterior.values() if name in p], dtype=float)
        if len(a) < 2 or len(b) < 2:
            continue
        std_a, std_b = a.std(ddof=0), b.std(ddof=0)
        rows.append(
            {
                "PARAMETER": name,
                "PRIOR_MEAN": a.mean(),
                "PRIOR_STD": std_a,
                "POST_MEAN": b.mean(),
                "POST_STD": std_b,
                "SHIFT_IN_PRIOR_STD": (b.mean() - a.mean()) / std_a if std_a > 0 else None,
                "STD_RATIO": std_b / std_a if std_a > 0 else None,
            }
        )
    out = pd.DataFrame(rows)
    if len(out):
        out[["SHIFT_IN_PRIOR_STD", "STD_RATIO"]] = out[["SHIFT_IN_PRIOR_STD", "STD_RATIO"]].astype(float)
    return out


def uncertain_parameters(params: dict[int, dict[str, float]]) -> int:
    """Count the parameters that vary over the members."""
    names = set().union(*(p.keys() for p in params.values())) if params else set()
    count = 0
    for name in names:
        values = {p[name] for p in params.values() if name in p}
        count += len(values) > 1
    return count


def mean_spread(summary: pd.DataFrame, label: str) -> float | None:
    """Mean over observations of var over members of SIMULATED / ERROR^2 (ddof 1), from obs_summary.

    SPREAD_RATIO is the member std (ddof 0) / ERROR, so s = SPREAD_RATIO^2 * COUNT / (COUNT - 1).
    None when no observation has two or more members.
    """
    if "SPREAD_RATIO" not in summary or "COUNT" not in summary:
        return None
    rows = summary[(summary["ENSEMBLE"] == label) & (summary["COUNT"] >= 2)]
    ratio, count = rows["SPREAD_RATIO"].astype(float), rows["COUNT"].astype(float)
    values = (ratio**2 * count / (count - 1)).dropna()
    values = values[np.isfinite(values)]
    return float(values.mean()) if len(values) else None


def ensemble_checks(
    misfit: pd.DataFrame, summary: pd.DataFrame, sizes: dict[str, int], n_params: int, args
) -> tuple[dict, dict, list[dict]]:
    """Trust gate and misfit vs chi-square for each ensemble. Return (gates, chi2, findings)."""
    gates, chi2 = {}, {}
    for label, size in sizes.items():
        gates[label] = collapse_checks.trust_gate(size, n_params, args.min_realizations)
        used = misfit[(misfit["ENSEMBLE"] == label) & (misfit["OBS_USED"] > 0)]["MISFIT"]
        n_obs = int(summary[(summary["ENSEMBLE"] == label) & (summary["COUNT"] > 0)].shape[0])
        mean = float(used.mean()) if len(used) else None
        chi2[label] = collapse_checks.misfit_vs_chi2(
            mean, n_obs, args.chi2_sigma, mean_spread(summary, label)
        )
    return gates, chi2, collapse_checks.findings(gates, chi2, "posterior")


def findings(
    summary: pd.DataFrame,
    misfit: pd.DataFrame,
    shift: pd.DataFrame | None,
    args,
    labels: list[str],
) -> list[dict]:
    """Hints from the tables. `labels` lists the ensembles in order: prior first, when there is a prior."""
    out: list[dict] = []
    for label, group in summary.groupby("ENSEMBLE", sort=False):
        # SPREAD_RATIO exists whenever an observation has a prediction (PREDICTIVE_CDF needs two members).
        if "SPREAD_RATIO" in group:
            # The predictive band (sim + noise), not min-max of the noise-free simulations: an observation carries
            # noise, so a correct ensemble misses many observations by min-max
            far = group["MEAN_RESIDUAL"].abs() > args.residual_limit
            # PREDICTIVE_CDF is undefined (NaN or absent) below two members; those rows are never "out of reach".
            cdf = (
                group["PREDICTIVE_CDF"] if "PREDICTIVE_CDF" in group else pd.Series(np.nan, index=group.index)
            )
            outside = group[cdf.notna() & ((cdf < REACH_TAIL) | (cdf > 1 - REACH_TAIL)) & far]
            if len(outside):
                out.append(
                    {
                        "ensemble": label,
                        "finding": "observation out of reach",
                        "detail": (
                            f"{len(outside)} of {len(group)} observations are outside P{100 * REACH_TAIL:g}-P{100 * (1 - REACH_TAIL):g}"
                            " of the predictive band (realizations plus observation noise)"
                            f" and more than {args.residual_limit} errors from the mean"
                        ),
                        "examples": outside["OBS"].head(5).tolist(),
                    }
                )
            collapsed = group[(group["SPREAD_RATIO"] < args.collapse_ratio) & far]
            if len(collapsed):
                out.append(
                    {
                        "ensemble": label,
                        "finding": "ensemble spread is much smaller than the error, and the mean misses the observation",
                        "detail": f"{len(collapsed)} observations: spread/error < {args.collapse_ratio} and |mean residual| > {args.residual_limit}",
                        "examples": collapsed["OBS"].head(5).tolist(),
                    }
                )
        skipped = group[group["COUNT"] == 0]
        if len(skipped):
            out.append(
                {
                    "ensemble": label,
                    "finding": "observation not used",
                    "detail": f"{len(skipped)} observations have no simulated value ({sorted(set(skipped['STATUS']))})",
                    "examples": skipped["OBS"].head(5).tolist(),
                }
            )
    if len(labels) == 2:
        prior_label, post_label = labels
        a = summary[summary["ENSEMBLE"] == prior_label].set_index("OBS")
        b = summary[summary["ENSEMBLE"] == post_label].set_index("OBS")
        common = a.index.intersection(b.index)
        both = common[(a.loc[common, "COUNT"] > 0) & (b.loc[common, "COUNT"] > 0)]
        if len(both):
            change = b.loc[both, "MEAN_RESIDUAL"].abs() - a.loc[both, "MEAN_RESIDUAL"].abs()
            worse = change[change > args.worse_margin].index
            single = change[change > args.worse_single].sort_values(ascending=False)
            with np.errstate(over="ignore", invalid="ignore"):
                prior_misfit = misfit[misfit["ENSEMBLE"] == prior_label]["MISFIT"].mean()
                post_misfit = misfit[misfit["ENSEMBLE"] == post_label]["MISFIT"].mean()
            require_finite(change, "findings residual change")
            require_finite([prior_misfit, post_misfit], "findings ensemble misfit means")
            if post_misfit > prior_misfit:
                out.append(
                    {
                        "ensemble": post_label,
                        "finding": "mean misfit is higher than in the prior",
                        "detail": f"{post_misfit:.3g} against {prior_misfit:.3g}",
                        "examples": [],
                    }
                )
            if len(worse) / len(both) > args.worse_fraction:
                out.append(
                    {
                        "ensemble": post_label,
                        "finding": "many observations moved away",
                        "detail": (
                            f"{len(worse)} of {len(both)} observations have a |mean residual| that is"
                            f" more than {args.worse_margin} errors larger than in the prior"
                        ),
                        "examples": worse[:5].tolist(),
                    }
                )
            if len(single):
                shown = [
                    f"{name}: {a.loc[name, 'MEAN_RESIDUAL']:.2f} -> {b.loc[name, 'MEAN_RESIDUAL']:.2f}"
                    for name in single.index[:5]
                ]
                out.append(
                    {
                        "ensemble": post_label,
                        "finding": "observation got clearly worse",
                        "detail": (
                            f"{len(single)} observations have a |mean residual| that is more than"
                            f" {args.worse_single} errors larger than in the prior"
                        ),
                        "examples": shown,
                    }
                )
    if shift is not None and len(shift):
        big = shift[shift["SHIFT_IN_PRIOR_STD"].abs() > args.shift_limit]
        if len(big):
            out.append(
                {
                    "ensemble": "posterior",
                    "finding": "large parameter shift",
                    "detail": f"{len(big)} parameters moved more than {args.shift_limit} prior standard deviations",
                    "examples": big["PARAMETER"].head(5).tolist(),
                }
            )
        narrow = shift[shift["STD_RATIO"] < args.std_ratio_limit]
        if len(narrow):
            out.append(
                {
                    "ensemble": "posterior",
                    "finding": "parameter spread collapsed",
                    "detail": f"{len(narrow)} parameters have a posterior std below {args.std_ratio_limit} of the prior std",
                    "examples": narrow["PARAMETER"].head(5).tolist(),
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--obs", action="append", required=True, type=Path, help="Observation file. Repeat for more."
    )
    parser.add_argument("--posterior", required=True, help="Quoted glob of runpaths")
    parser.add_argument("--prior", default=None, help="Quoted glob of runpaths of the prior")
    parser.add_argument(
        "--eclbase",
        default=None,
        help="ERT ECLBASE, for example model/DROGON-<IENS>, relative to the runpath; or a deck name",
    )
    parser.add_argument(
        "--gen-data",
        action="append",
        default=[],
        metavar="NAME=RESULT_FILE",
        help="GEN_DATA result file relative to the runpath; %%d is the report step. Repeat for more.",
    )
    parser.add_argument(
        "--ert-config", type=Path, default=None, help="ERT config. Its GEN_DATA lines give the result files."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--expected", type=int, default=None, help="Expected number of realizations, numbered 0 to N-1"
    )
    group.add_argument(
        "--realizations", default=None, help="Expected realizations as an ERT spec, for example 0-4,7"
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--collapse-ratio", type=float, default=0.1)
    parser.add_argument("--residual-limit", type=float, default=2.0)
    parser.add_argument("--worse-fraction", type=float, default=0.3)
    parser.add_argument("--worse-margin", type=float, default=0.5)
    parser.add_argument("--worse-single", type=float, default=1.0)
    parser.add_argument("--shift-limit", type=float, default=2.0)
    parser.add_argument("--std-ratio-limit", type=float, default=0.2)
    parser.add_argument(
        "--min-realizations", type=int, default=20, help="Below this ensemble size the result is weak"
    )
    parser.add_argument(
        "--chi2-sigma", type=float, default=3.0, help="Width of the noise band of the misfit, in std"
    )
    parser.add_argument(
        "--update-log",
        action="append",
        default=[],
        type=Path,
        help="ERT update log folder or Report file. Joins its facts into the findings. Repeat for more.",
    )
    parser.add_argument(
        "--ratio-low",
        type=float,
        default=0.1,
        help="Update log: low response_std/error of an active observation",
    )
    args = parser.parse_args(argv)
    if not np.isfinite(args.ratio_low) or args.ratio_low < 0:
        print("ERROR: --ratio-low must be finite and nonnegative", file=sys.stderr)
        return 2
    if args.min_realizations < 1 or not np.isfinite(args.chi2_sigma) or args.chi2_sigma <= 0:
        print("ERROR: --min-realizations and --chi2-sigma must be positive", file=sys.stderr)
        return 2
    update_log = None
    if args.update_log:
        try:
            update_log = update_log_report.load(args.update_log, args.ratio_low)
        except FileNotFoundError as error:
            print(f"ERROR: --update-log: {error}", file=sys.stderr)
            return 2
        except (OSError, ValueError, IndexError) as error:
            print(f"ERROR: --update-log: {error}", file=sys.stderr)
            return 1

    bounded = {"collapse_ratio", "std_ratio_limit", "worse_fraction"}
    for name in (*sorted(bounded), "residual_limit", "worse_margin", "worse_single", "shift_limit"):
        value = getattr(args, name)
        if not np.isfinite(value) or value < 0 or (name in bounded and value > 1):
            domain = "in [0, 1]" if name in bounded else "nonnegative"
            print(f"ERROR: --{name.replace('_', '-')} must be finite and {domain}", file=sys.stderr)
            return 2

    observations: list[dict] = []
    general: list[dict] = []
    for path in args.obs:
        if not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2
        for record in obs_qc.parse_file(path):
            if record["kind"] == "SUMMARY_OBSERVATION":
                observations.append(obs_qc.normalize_summary(record))
            elif record["kind"] == "GENERAL_OBSERVATION":
                general.append(obs_qc.normalize_general(record))
    if args.ert_config is not None and not args.ert_config.is_file():
        print(f"ERROR: --ert-config is not a file: {args.ert_config}", file=sys.stderr)
        return 2
    try:
        result_files = gen_data_sources(args.gen_data, args.ert_config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    general_unused = []
    if general and not (args.gen_data or args.ert_config):
        general_unused = [o["name"] for o in general]
        print(f"NOTE: GENERAL_OBSERVATION not used (give --ert-config or --gen-data): {general_unused}")
        general = []
    for o in general:
        if o["data"] not in result_files:
            o["issues"].append({"severity": "error", "message": f"no GEN_DATA RESULT_FILE for {o['data']}"})
        if o["report_step"] is None:
            o["issues"].append({"severity": "error", "message": "needs RESTART to find the GEN_DATA file"})
    bad = [o for o in observations + general if any(i["severity"] == "error" for i in o["issues"])]
    if bad:
        for o in bad[:10]:
            print(f"ERROR: {o['file']}:{o['line']} {o['name']}: {o['issues']}", file=sys.stderr)
        print("Fix the observations first. Run obs_qc.py for the full list.", file=sys.stderr)
        return 1
    keys = sorted({o["key"] for o in observations})
    for o in general:
        observations += general_points(o, result_files[o["data"]])
    gen_files = {(o["data"], o["report_step"]): result_files[o["data"]] for o in general}
    if not observations:
        print("ERROR: no SUMMARY_OBSERVATION or usable GENERAL_OBSERVATION found", file=sys.stderr)
        return 2

    try:
        expected = runpath_lib.expected_set(args.expected, args.realizations)
    except ValueError as error:
        print(f"ERROR: --realizations: {error}", file=sys.stderr)
        return 2
    ensembles = [("posterior", args.posterior)]
    if args.prior:
        ensembles.insert(0, ("prior", args.prior))
    output = args.outdir.resolve()
    obs_sources = [p.resolve() for p in args.obs]
    matched = {
        label: runpath_lib.read_ensemble(pattern, need_data_file=False) for label, pattern in ensembles
    }
    runpath_trees = [
        path.resolve()
        for ensemble in matched.values()
        for path in [
            *[member.runpath for member in ensemble.found],
            *[drop.path for drop in ensemble.dropped],
        ]
    ]
    if any(output == p or output in p.parents for p in obs_sources) or any(
        output == t or t in output.parents or output in t.parents for t in runpath_trees
    ):
        print("ERROR: --outdir overlaps an input tree or contains an input file", file=sys.stderr)
        return 2
    loaded = {}
    all_issues: list[dict] = []
    missing_by_ensemble: dict[str, list[int]] = {}
    extra_by_ensemble: dict[str, list[int]] = {}
    for label, pattern in ensembles:
        if not matched[label].found and not matched[label].dropped:
            print(f"ERROR: no runpath matches ({label}): {pattern}", file=sys.stderr)
            return 2
        frames, params, issues, missing, extra = load_ensemble(
            label, pattern, keys, args.eclbase, expected, gen_files
        )
        loaded[label] = (frames, params)
        all_issues += issues
        missing_by_ensemble[label] = missing
        extra_by_ensemble[label] = extra
        if extra:
            print(f"NOTE: {label}: realization folders outside the expected set are not used: {extra}")
    if args.prior and set(loaded["prior"][0]) != set(loaded["posterior"][0]):
        all_issues.append(
            {"ensemble": "comparison", "realization": None, "issue": "prior and posterior member sets differ"}
        )
    fatal = [item for item in all_issues if item.get("fatal")]
    if fatal:
        for item in fatal:
            print(
                f"ERROR: {item['ensemble']} realization {item['realization']}: {item['issue']}",
                file=sys.stderr,
            )
        return 1
    empty = [label for label, (frames, _) in loaded.items() if not frames]
    if empty:
        for label in empty:
            items = [item for item in all_issues if item["ensemble"] == label]
            for item in items[:5]:
                print(f"ERROR: {label} realization {item['realization']}: {item['issue']}", file=sys.stderr)
            if len(items) > 5:
                print(f"ERROR: {label}: {len(items) - 5} more member issues", file=sys.stderr)
            print(f"ERROR: the {label} ensemble has no valid members", file=sys.stderr)
        return 1
    units = None
    for label, (frames, _) in loaded.items():
        for real, frame in frames.items():
            native = frame.attrs.get("units", {})
            if not all(unit_is_valid(key, native.get(key)) for key in keys):
                print(f"ERROR: {label} realization {real}: native vector units unavailable", file=sys.stderr)
                return 1
            if units is not None and native != units:
                print(
                    f"ERROR: {label} realization {real}: native vector units differ: {native} vs {units}",
                    file=sys.stderr,
                )
                return 1
            units = native
    if (all_issues or any(missing_by_ensemble.values())) and not args.allow_partial:
        for item in all_issues:
            print(
                f"ERROR: {item['ensemble']} realization {item['realization']}: {item['issue']}",
                file=sys.stderr,
            )
        for label, missing in missing_by_ensemble.items():
            if missing:
                print(f"ERROR: {label} missing realizations: {missing}", file=sys.stderr)
        print("Use --allow-partial to continue with the valid members.", file=sys.stderr)
        return 1

    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        table = pd.concat(
            [residual_table(label, loaded[label][0], observations) for label, _ in ensembles],
            ignore_index=True,
        )
    try:
        missing_prediction = table["STATUS"].isin(MISSING_STATUS)
        validate_numeric(table, "residuals", {name: missing_prediction for name in ("SIMULATED", "RESIDUAL")})
    except IntegrityError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if table["RESIDUAL"].notna().sum() == 0:
        print("ERROR: no observation has a simulated value. Check the keys and the dates.", file=sys.stderr)
        return 1
    invalid = table[table["RESIDUAL"].isna()]
    if len(invalid):
        all_issues.append(
            {
                "ensemble": "coverage",
                "realization": None,
                "issue": f"{len(invalid)} member-observation predictions missing",
            }
        )
        if not args.allow_partial:
            print(
                "ERROR: incomplete observation predictions; refusing an incomparable misfit", file=sys.stderr
            )
            return 1
    try:
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            require_finite(table["RESIDUAL"].dropna().to_numpy() ** 2, "squared residuals")
            summary = obs_summary(table)
            summary_nullable = {
                name: summary["COUNT"] == 0
                for name in (
                    "SIM_MEAN",
                    "SIM_STD",
                    "SIM_MIN",
                    "SIM_MAX",
                    "MEAN_RESIDUAL",
                    "SPREAD_RATIO",
                )
            }
            # The predictive CDF needs at least two members (coverage.predictive_cdf domain).
            summary_nullable.update(
                {name: summary["COUNT"] < 2 for name in ("PREDICTIVE_CDF", "INSIDE_PREDICTIVE_P5_P95")}
            )
            validate_numeric(summary, "obs_summary", summary_nullable)
            misfit = real_misfit(table)
            validate_numeric(misfit, "real_misfit", {"MISFIT": misfit["OBS_USED"] == 0})
            shift = parameter_shift(loaded["prior"][1], loaded["posterior"][1]) if args.prior else None
            if shift is not None and len(shift):
                validate_numeric(
                    shift,
                    "parameter_shift",
                    {name: shift["PRIOR_STD"] == 0 for name in ("SHIFT_IN_PRIOR_STD", "STD_RATIO")},
                )
            families = family_misfit(table)
            found = findings(summary, misfit, shift, args, [label for label, _ in ensembles])
            sizes = {label: len(loaded[label][0]) for label, _ in ensembles}
            n_params = uncertain_parameters(loaded[ensembles[0][0]][1])
            gates, chi2, size_findings = ensemble_checks(misfit, summary, sizes, n_params, args)
            caveat = collapse_checks.collapse_caveat(min(sizes.values()), n_params)
            for item in found:
                if item.get("finding") == "parameter spread collapsed" and caveat:
                    item["detail"] += f". {caveat}"
            found = size_findings + found
            if update_log is not None:
                found += update_log_report.join_findings(update_log, found)
            reports = []
            for label, _ in ensembles:
                m = misfit[(misfit["ENSEMBLE"] == label) & (misfit["OBS_USED"] > 0)]["MISFIT"]
                n_obs = int(summary[(summary["ENSEMBLE"] == label) & (summary["COUNT"] > 0)].shape[0])
                prefix = f"{label}: {len(loaded[label][0])} realizations, {n_obs} of {len(observations)} observations used,"
                if m.empty:
                    reports.append(f"{prefix} misfit unavailable (no usable predictions)")
                    continue
                statistics = [m.mean(), m.median(), m.min(), m.max()]
                require_finite(statistics, f"{label} report misfit statistics")
                reports.append(
                    prefix
                    + f" misfit mean {statistics[0]:.3g} median {statistics[1]:.3g} min {statistics[2]:.3g} max {statistics[3]:.3g}"
                )
    except (ValueError, OverflowError, FloatingPointError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    manifest = {
        "observation_files": [str(p) for p in args.obs],
        "observations": len(observations),
        "keys": keys,
        "general_observations": {
            "used": [
                {
                    "name": o["name"],
                    "data": o["data"],
                    "report_step": o["report_step"],
                    "result_file": result_files[o["data"]],
                    "points": o["count"],
                }
                for o in general
            ],
            "not_used": general_unused,
        },
        "units": units,
        "observation_units_assumption": (
            "Observation values and errors are assumed to use the same native units as their summary vectors; "
            "the observation format has no unit field. No unit conversion is performed."
        ),
        "ensembles": {
            label: {
                "glob": pattern,
                "realizations_used": sorted(loaded[label][0]),
                "missing": missing_by_ensemble[label],
                "extra_not_used": extra_by_ensemble[label],
            }
            for label, pattern in ensembles
        },
        "issues": all_issues,
        "time_rule": {key: time_rule(key) for key in keys},
        "time_rule_definition": (
            "step: value at the end of the time step that holds the date (rates and ratios). "
            "linear: linear interpolation between time steps (other vectors)"
        ),
        "misfit_definition": "mean of squared normalized residuals, (simulated - observed) / error",
        "thresholds": {
            "collapse_ratio": args.collapse_ratio,
            "residual_limit": args.residual_limit,
            "worse_fraction": args.worse_fraction,
            "worse_margin": args.worse_margin,
            "worse_single": args.worse_single,
            "shift_limit": args.shift_limit,
            "std_ratio_limit": args.std_ratio_limit,
            "min_realizations": args.min_realizations,
            "chi2_sigma": args.chi2_sigma,
            "ratio_low": args.ratio_low,
        },
        "uncertain_parameters": n_params,
        "misfit_by_family": families,
        # Coverage is computed on the authoritative inventory (configured observations x expected members);
        # an incomplete partial run gets a coverage error entry instead of a share over the surviving cells.
        "coverage_p5_p95": coverage_block(
            table,
            [label for label, _ in ensembles],
            expected_obs={o["name"] for o in observations},
            expected_members={
                label: set(expected) if expected is not None else set(loaded[label][0])
                for label, _ in ensembles
            },
        ),
        "trust": gates,
        "misfit_vs_chi2": chi2,
        "update_log": update_log_report.compact(update_log) if update_log is not None else None,
        "findings": found,
        "pandas_version": pd.__version__,
    }
    try:
        manifest_text = json.dumps(manifest, indent=2, allow_nan=False)
    except (ValueError, TypeError) as error:
        print(f"ERROR: invalid manifest: {error}", file=sys.stderr)
        return 1
    args.outdir.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.outdir / "residuals.csv", index=False)
    summary.to_csv(args.outdir / "obs_summary.csv", index=False)
    misfit.to_csv(args.outdir / "real_misfit.csv", index=False)
    if shift is not None:
        shift.to_csv(args.outdir / "params_shift.csv", index=False)
    (args.outdir / "manifest.json").write_text(manifest_text)

    for line in reports:
        print(line)
    for label, _ in ensembles:
        rows = families.get(label, {})
        parts = [
            f"{name} {row['observations']} obs {row['mean_squared_residual']:.3g}"
            for name, row in rows.items()
        ]
        print(f"FAMILY [{label}] observations and mean squared residual: {', '.join(parts)}")
    for label, gate in gates.items():
        print(
            f"GATE [{label}] ensemble size: {gate['level']} (N = {gate['realizations']}, {n_params} parameters)"
        )
    for label, item in chi2.items():
        if item["verdict"] != "unavailable":
            band = f"{item['band'][0]:.3g}-{item['band'][1]:.3g}"
            line = f"CHI2 [{label}] mean misfit {item['mean_misfit']:.3g}: {item['verdict']} {band}"
            if item.get("spread_verdict", "unavailable") != "unavailable":
                line += (
                    f"; {item['spread_verdict']} {item['spread_upper']:.3g} = 1 + 2 s + k sqrt(2/n)"
                    f" (s {item['spread']:.3g})"
                )
                if item["verdict"] == "above the noise band" and item["spread_verdict"].startswith("inside"):
                    line += ". Above the noise-only band is context here: the ensemble spread explains it"
            print(line)
    for label, item in manifest["coverage_p5_p95"].items():
        if "predictive" in item:
            print(
                f"COVERAGE [{label}] predictive P5-P95 {item['predictive']:.2f} (nominal {item['nominal']:.2f}),"
                f" noise-biased {item['noise_biased_do_not_quote']:.2f} (do not quote)"
            )
        else:
            # Never silent; the exit code is left to the inventory and prediction checks above.
            print(f"WARNING: coverage not computed: {item['error']}", file=sys.stderr)
    for item in found:
        print(f"FINDING [{item['ensemble']}] {item['finding']}: {item['detail']} {item['examples']}")
    print(f"Wrote {args.outdir}")
    return 1 if (all_issues or any(missing_by_ensemble.values())) else 0


if __name__ == "__main__":
    sys.exit(main())
