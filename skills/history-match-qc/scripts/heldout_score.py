#!/usr/bin/env python3
"""Retrospective held-out diagnostics from a hashed ahm-heldout/1 manifest.

Usage: heldout_score.py --split split.json --out <fresh-directory>
Only explicit SUMMARY_OBSERVATION assimilation files are supported. No simulation,
native reader, training provenance certification, acceptance threshold, or verdict.
NumPy and pandas are required for scoring, but not for --help.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

CONVENTION = {"latent_truth": "linear", "noisy_observation": "inverse_cdf"}
OBS_FIELDS = {"OBS", "KEY", "DATE", "UNIT", "OBSERVED", "ERROR", "kind"}
COLUMNS = {"ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "UNIT", "OBSERVED", "ERROR", "SIMULATED"}
LIMITS = (
    "Current files establish retrospective split validation only, not execution-time independence. "
    "They cannot certify absence of historical training leakage, model selection on heldout data, "
    "or tuning before the manifest was recorded. Correlated dates are not independent trials. "
    "Units and reference kinds are caller declarations, not verified against native simulator files. "
    "No cross-unit pooled score or acceptance verdict is defined."
)


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label}: expected exactly fields {sorted(expected)}")


def _text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty string without surrounding whitespace")
    return value


def _number(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _day(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} must be an ISO date YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO date YYYY-MM-DD") from error


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON field {key}")
        out[key] = value
    return out


def _module(filename):
    path = Path(__file__).resolve().with_name(filename)
    name = "_heldout_" + path.stem + "_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _bound_file(reference, folder, label):
    _fields(reference, {"path", "sha256"}, label)
    token = _text(reference["path"], f"{label}.path")
    if Path(token).is_absolute():
        raise ValueError(f"{label}.path must be relative to the manifest")
    digest = reference["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}.sha256 must be 64 lowercase hex digits")
    path = (folder / token).resolve(strict=True)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"{label}: sha256 mismatch or not a file")
    return path


def _manifest(path):
    manifest = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    _fields(
        manifest,
        {
            "schema",
            "history_end",
            "ensembles",
            "assimilation",
            "residuals",
            "history",
            "heldout",
            "error_assumption",
            "percentile_convention",
        },
        "split",
    )
    if manifest["schema"] != "ahm-heldout/1":
        raise ValueError("unsupported split schema")
    cutoff = _day(manifest["history_end"], "history_end")
    if manifest["error_assumption"] != "independent_gaussian_sd":
        raise ValueError("error_assumption must be independent_gaussian_sd")
    if manifest["percentile_convention"] != CONVENTION:
        raise ValueError(f"percentile_convention must be {CONVENTION}")
    ensembles = manifest["ensembles"]
    if not isinstance(ensembles, dict) or not ensembles:
        raise ValueError("ensembles must declare exact members for at least one ensemble")
    for ensemble, members in ensembles.items():
        _text(ensemble, "ensemble")
        if (
            not isinstance(members, list)
            or len(members) < 2
            or any(type(m) is not int or m < 0 or m > 2**53 - 1 for m in members)
            or len(set(members)) != len(members)
        ):
            raise ValueError(
                f"{ensemble}: members must be unique nonnegative integer IDs, at least two, <= 2^53-1"
            )
    observations, physical, key_units = {}, set(), {}
    for split in ("history", "heldout"):
        declared = manifest[split]
        if not isinstance(declared, list) or not declared:
            raise ValueError(f"{split} needs at least one observation")
        for obs in declared:
            _fields(obs, OBS_FIELDS, f"{split} observation")
            for field in ("OBS", "KEY", "UNIT", "kind"):
                _text(obs[field], field)
            when = _day(obs["DATE"], "DATE")
            _number(obs["OBSERVED"], "OBSERVED")
            _number(obs["ERROR"], "ERROR", positive=True)
            if obs["kind"] not in CONVENTION:
                raise ValueError("kind must be latent_truth or noisy_observation")
            if obs["OBS"] in observations:
                raise ValueError("duplicate OBS identity across declared splits")
            ident = (obs["KEY"], obs["DATE"])
            if ident in physical:
                raise ValueError("duplicate physical KEY/DATE across declared splits")
            physical.add(ident)
            if obs["KEY"] in key_units and key_units[obs["KEY"]] != obs["UNIT"]:
                raise ValueError("UNIT must be consistent for each KEY across splits")
            key_units[obs["KEY"]] = obs["UNIT"]
            observations[obs["OBS"]] = {**obs, "split": split}
    for obs in observations.values():
        when = _day(obs["DATE"], "DATE")
        if (obs["split"] == "history" and when > cutoff) or (obs["split"] == "heldout" and when <= cutoff):
            raise ValueError("history dates must be <= history_end, heldout dates must be > history_end")
    files = {
        label: _bound_file(manifest[label], path.parent, label) for label in ("assimilation", "residuals")
    }
    if len({path.resolve(), *files.values()}) != 3:
        raise ValueError("manifest, assimilation and residuals must be distinct input files")
    return manifest, observations, files


def _assimilation(path, observations):
    parser = _module("obs_qc.py")
    text = parser.strip_comments(path.read_text())
    if re.search(r"\bINCLUDE\b", text, re.I):
        raise ValueError("assimilation INCLUDE is unsupported; supply one fully expanded hashed .obs file")
    if parser.BLOCK_RE.sub("", text).strip():
        raise ValueError("assimilation contains unsupported or unparsed syntax")
    records = parser.parse_file(path)
    expected = {n: o for n, o in observations.items() if o["split"] == "history"}
    heldout = {(o["KEY"], o["DATE"]) for o in observations.values() if o["split"] == "heldout"}
    actual, physical, measured = {}, set(), {}
    matches = list(parser.BLOCK_RE.finditer(text))
    for record, match in zip(records, matches, strict=True):
        if record["kind"] != "SUMMARY_OBSERVATION" or record["has_segments"]:
            raise ValueError(f"unsupported assimilation observation type {record['kind']}")
        fields = list(parser.FIELD_RE.finditer(match["body"]))
        if len({m["field"] for m in fields}) != len(fields):
            raise ValueError("duplicate assimilation observation field")
        if parser.FIELD_RE.sub("", match["body"]).strip():
            raise ValueError("unsupported assimilation observation syntax")
        if set(record["fields"]) - {"KEY", "DATE", "VALUE", "ERROR", "ERROR_MODE", "ERROR_MIN"}:
            raise ValueError("unsupported assimilation observation fields")
        obs = parser.normalize_summary(record)
        if any(i["severity"] == "error" for i in obs["issues"]):
            raise ValueError(f"assimilation {obs['name']}: {obs['issues']}")
        when = datetime.fromisoformat(obs["date"])
        if when.tzinfo is not None or when.time() != datetime.min.time():
            raise ValueError("assimilation DATE must be a day without time or timezone")
        ident = (obs["key"].strip('"'), when.date().isoformat())
        if ident in heldout:
            raise ValueError("assimilation leaks a heldout physical KEY/DATE, regardless of OBS name")
        if obs["name"] in actual or ident in physical:
            raise ValueError("duplicate assimilation OBS or physical KEY/DATE")
        actual[obs["name"]] = ident
        measured[obs["name"]] = (obs["value"], obs["error"])
        physical.add(ident)
    if actual != {n: (o["KEY"], o["DATE"]) for n, o in expected.items()}:
        raise ValueError(
            "actual assimilation observations differ from declared history OBS/KEY/DATE identities"
        )
    for name, (value, error) in measured.items():
        for field, actual_value, declared in (
            ("VALUE", value, expected[name]["OBSERVED"]),
            ("ERROR", error, expected[name]["ERROR"]),
        ):
            if not math.isclose(actual_value, declared, rel_tol=1e-9, abs_tol=0.0):
                raise ValueError(
                    f"{name}: declared {declared} differs from the actual assimilation {field} {actual_value}"
                )


def _mean(values):
    return math.fsum(values) / len(values)


def score_distribution(samples, reference, error, kind):
    """Equal-weight finite ensemble CRPS and inclusive central 90% interval diagnostics.

    Latent truth uses empirical CRPS and linear sample percentiles, without noise.
    Noisy observations use sim + N(0, ERROR), analytic mixture CRPS, inverse-CDF bands.
    ERROR is always a positive native-unit standard deviation, even for latent truth.
    """
    values = sorted(_number(x, "SIMULATED") for x in samples)
    if len(values) < 2:
        raise ValueError("at least two samples required")
    reference, error = _number(reference, "OBSERVED"), _number(error, "ERROR", positive=True)
    if kind not in CONVENTION:
        raise ValueError("unsupported reference kind")
    n = len(values)
    bias = _mean(values) - reference
    if kind == "latent_truth":

        def quantile(p):
            index = (n - 1) * p
            low = int(index)
            fraction = index - low
            return (1 - fraction) * values[low] + fraction * values[min(low + 1, n - 1)]

        lo, hi = quantile(0.05), quantile(0.95)
        crps = _mean([abs(v - reference) for v in values]) - math.fsum(
            ((2 * i - n + 1) / n) * (v / n) for i, v in enumerate(values)
        )
    else:

        def absolute_normal(delta, sigma):
            z = delta / sigma
            return sigma * math.sqrt(2 / math.pi) * math.exp(-0.5 * z * z) + delta * math.erf(
                z / math.sqrt(2)
            )

        def quantile(p):
            low, high = values[0] - 10 * error, values[-1] + 10 * error
            if not math.isfinite(low) or not math.isfinite(high):
                raise ValueError("predictive quantile bounds must be finite")
            for _ in range(120):
                mid = low / 2 + high / 2
                cdf = _mean([0.5 * math.erfc((v - mid) / error / math.sqrt(2)) for v in values])
                if cdf < p:
                    low = mid
                else:
                    high = mid
            return low / 2 + high / 2

        lo, hi = quantile(0.05), quantile(0.95)
        first = _mean([absolute_normal(v - reference, error) for v in values])
        second = math.fsum(
            absolute_normal(a - b, math.sqrt(2) * error) / n / n / 2 for a in values for b in values
        )
        crps = first - second
    result = {
        "bias": bias,
        "coverage90": float(lo <= reference <= hi),
        "width90": hi - lo,
        "crps": max(0.0, crps),
    }
    if not math.isfinite(crps) or not all(math.isfinite(v) for v in result.values()):
        raise ValueError("computed scores must be finite")
    return result


def score(split: Path):
    """Validate the entire bound input bundle and return a retrospective diagnostic report."""
    import pandas as pd

    manifest, observations, files = _manifest(split)
    _assimilation(files["assimilation"], observations)
    with files["residuals"].open(newline="") as stream:
        columns = next(csv.reader(stream), [])
    if (
        len(columns) != len(set(columns))
        or not COLUMNS <= set(columns)
        or set(columns) - COLUMNS - {"STATUS", "RESIDUAL"}
    ):
        raise ValueError("residuals CSV fields must be the required schema plus optional STATUS and RESIDUAL")
    table = pd.read_csv(
        files["residuals"],
        keep_default_na=False,
        float_precision="round_trip",
        dtype={c: str for c in ("ENSEMBLE", "OBS", "KEY", "DATE", "UNIT", "STATUS")},
    )
    for column in ("REAL", "OBSERVED", "ERROR", "SIMULATED", "RESIDUAL"):
        if column in table:
            try:
                table[column] = pd.to_numeric(table[column], errors="raise")
            except (ValueError, TypeError) as error:
                raise ValueError(f"{column} must be finite numeric") from error
            if not table[column].map(math.isfinite).all():
                raise ValueError(f"{column} must be finite numeric")
    if set(table["ENSEMBLE"]) != set(manifest["ensembles"]):
        raise ValueError("residual ensemble set differs from declared ensemble set")
    if "STATUS" in table and not set(table["STATUS"]) <= {"exact", "interpolated", "step"}:
        raise ValueError("STATUS must be exact, interpolated or step for every usable prediction")
    validator = _module("coverage.py")
    for ensemble, members in manifest["ensembles"].items():
        validator.from_residuals(
            table, ensemble, expected_members=set(members), expected_obs=set(observations)
        )
    for name, obs in observations.items():
        rows = table[table["OBS"] == name]
        for column in ("KEY", "DATE", "UNIT", "OBSERVED", "ERROR"):
            if not (rows[column] == obs[column]).all():
                raise ValueError(f"{name}: {column} differs from declared observation metadata")
    if "RESIDUAL" in table:
        expected = (table["SIMULATED"] - table["OBSERVED"]) / table["ERROR"]
        if any(
            not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
            for a, b in zip(table["RESIDUAL"], expected, strict=True)
        ):
            raise ValueError("RESIDUAL differs from (SIMULATED - OBSERVED) / ERROR")
    grouped = defaultdict(list)
    for ensemble in sorted(manifest["ensembles"]):
        wide = table[table["ENSEMBLE"] == ensemble].pivot(index="REAL", columns="OBS", values="SIMULATED")
        for name, obs in sorted(observations.items()):
            point = score_distribution(wide[name].tolist(), obs["OBSERVED"], obs["ERROR"], obs["kind"])
            point["normalized_bias"] = point["bias"] / obs["ERROR"]
            grouped[(ensemble, obs["split"], obs["KEY"], obs["UNIT"], obs["kind"])].append(point)
    scores = []
    for (ensemble, split_name, key, unit, kind), points in sorted(grouped.items()):
        row = {
            "ensemble": ensemble,
            "split": split_name,
            "KEY": key,
            "UNIT": unit,
            "kind": kind,
            "n_obs": len(points),
            "n_members": len(manifest["ensembles"][ensemble]),
        }
        for metric in ("bias", "coverage90", "width90", "crps"):
            row[metric] = _mean([p[metric] for p in points])
        for metric, field in (("rms", "bias"), ("normalized_rms", "normalized_bias")):
            row[metric] = math.hypot(*(p[field] for p in points)) / math.sqrt(len(points))
        if not all(
            math.isfinite(row[k]) for k in ("bias", "rms", "normalized_rms", "coverage90", "width90", "crps")
        ):
            raise ValueError("computed aggregate scores must be finite")
        scores.append(row)
    for label in files:
        _bound_file(manifest[label], split.parent, label)
    return {
        "schema": "ahm-heldout-report/1",
        "scope": "retrospective split validation",
        "limits": LIMITS,
        "history_end": manifest["history_end"],
        "manifest": manifest,
        "scores": scores,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, required=True, help="fresh output directory outside all input directories"
    )
    args = parser.parse_args(argv)
    try:
        split = args.split.resolve(strict=True)
        _, _, files = _manifest(split)
        out = args.out.resolve()
        if out.exists() or args.out.is_symlink():
            raise ValueError("--out must be a fresh directory")
        for source in (split, *files.values()):
            if out.is_relative_to(source.parent) or source.is_relative_to(out):
                raise ValueError("--out must be outside input directories and must not contain inputs")
        report = score(split)
        payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
        out.mkdir(parents=True, exist_ok=False)
        (out / "heldout.json").write_text(payload)
    except (OSError, ValueError, TypeError, KeyError, ImportError, OverflowError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(out / "heldout.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
