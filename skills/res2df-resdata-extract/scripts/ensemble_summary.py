#!/usr/bin/env python3
"""Extract summary vectors from an ERT ensemble with strict checks.

Needs res2df and pandas (both are in Komodo). Reads simulator files. Writes only to --outdir.

Usage:
    ensemble_summary.py '/scratch/fmu/user/case/realization-*/iter-0' \
        --keys FOPT FWPT 'WOPT:*' --time-index yearly --outdir report/ --expected 100
    Use --realizations 0-4,7 instead of --expected when the run used a subset.
    A key of --keys that a member does not have is an error. A key of --optional-keys that no member
    has is dropped with a warning. The manifest lists it.

Outputs in --outdir:
    summary_realizations.csv   one row for each realization and date
    summary_stats.csv          COUNT, MEAN, P10, P50, P90 for each date and vector
    manifest.json              inputs, versions, missing members (no runpath folder), issues of failed members,
                               percentile convention

Percentiles are numerical. P10 is the 10th percentile of the values. It is not the
volumetric convention, where P10 is the high case.
Exit code: 0 = success, 1 = some members failed, 2 = usage or input error.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import numpy as np
import pandas as pd
import runpath_lib
from res2df import ResdataFiles as SimFiles
from res2df import summary as summary_module

VALID_TIME_INDEX = {"raw", "daily", "monthly", "yearly", "first", "last"}


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


def read_units(data_file: Path, vectors: list[str]) -> dict[str, str | None]:
    """Units come from the SMSPEC file of one member. A vector without a unit gets None."""
    units: dict[str, str | None] = {}
    try:
        smry = SimFiles(str(data_file)).get_summary()
    except Exception:
        return {vector: None for vector in vectors}
    for vector in vectors:
        try:
            unit = smry.unit(vector)
            units[vector] = unit.strip() if isinstance(unit, str) else unit
        except Exception:
            units[vector] = None
    return units


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("runpaths", help="Quoted glob of runpaths, one for each realization")
    parser.add_argument("--keys", nargs="+", default=[], help="Summary vector names or wildcards")
    parser.add_argument(
        "--optional-keys",
        nargs="+",
        default=[],
        help="Keys that are dropped with a warning when no member has them. For defaults of a caller",
    )
    parser.add_argument("--time-index", required=True, choices=sorted(VALID_TIME_INDEX))
    parser.add_argument(
        "--eclbase", default=None, help="Base name, when a runpath has more than one .DATA file"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--expected", type=int, default=None, help="Expected number of realizations, numbered 0 to N-1"
    )
    group.add_argument(
        "--realizations", default=None, help="Expected realizations as an ERT spec, for example 0-4,7"
    )
    parser.add_argument("--allow-partial", action="store_true", help="Continue when members fail")
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args(argv)
    if not args.keys and not args.optional_keys:
        parser.error("give --keys or --optional-keys")
    all_keys = [*args.keys, *(key for key in args.optional_keys if key not in args.keys)]

    try:
        expected = runpath_lib.expected_set(args.expected, args.realizations)
    except ValueError as error:
        print(f"ERROR: --realizations: {error}", file=sys.stderr)
        return 2
    ensemble = runpath_lib.read_ensemble(args.runpaths, args.eclbase, expected)
    runpaths = [member.runpath for member in ensemble.found] + [
        drop.path for drop in (*ensemble.dropped, *ensemble.extra)
    ]
    output = Path(args.outdir).resolve()
    resolved_runpaths = [p.resolve() for p in runpaths]
    if any(output == p or output in p.parents or p in output.parents for p in resolved_runpaths):
        print("ERROR: --outdir overlaps a runpath", file=sys.stderr)
        return 2
    if not runpaths:
        print(f"ERROR: no runpath matches: {args.runpaths}", file=sys.stderr)
        return 2
    numbers = [member.realization for member in ensemble.found]
    if any(drop.realization is None for drop in ensemble.dropped) or len(set(numbers)) != len(numbers):
        for drop in ensemble.dropped:
            print(f"ERROR: {drop.path}: {drop.reason}", file=sys.stderr)
        print("ERROR: runpaths must have one unique 'realization-<n>' each", file=sys.stderr)
        return 2
    extra = sorted(drop.realization for drop in ensemble.extra)

    reference_index = None
    reference_columns = None
    reference_units = None
    reference_period = None
    frames: list[pd.DataFrame] = []
    issues: list[dict] = [
        {"realization": drop.realization, "issue": drop.reason} for drop in ensemble.dropped
    ]
    sources: list[dict] = []
    for member in ensemble.found:
        real, data_file = member.realization, member.data_file
        assert data_file is not None
        unsmry = data_file.with_suffix(".UNSMRY")
        if not unsmry.is_file():
            issues.append({"realization": real, "issue": f"missing {unsmry.name}"})
            continue
        try:
            frame = summary_module.df(
                SimFiles(str(data_file)), time_index=args.time_index, column_keys=all_keys
            )
        except Exception as error:  # report each failure with its cause
            issues.append({"realization": real, "issue": f"{type(error).__name__}: {error}"})
            continue
        if frame.empty:
            issues.append({"realization": real, "issue": "no vector matches --keys"})
            continue
        try:
            if pd.api.types.is_numeric_dtype(frame.index.dtype):
                raise ValueError("summary dates cannot be numeric")
            frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
            if frame.index.hasnans:
                raise ValueError("dates must be a valid DatetimeIndex")
            if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
                raise ValueError("dates must be unique and increasing")
            for pattern in args.keys:
                if not any(fnmatch.fnmatchcase(key, pattern) for key in frame.columns):
                    raise ValueError(f"no vector matches requested key {pattern}")
            values = frame.to_numpy(dtype=float)
            if not np.isfinite(values).all():
                raise ValueError("summary contains nonfinite values")
            smry = SimFiles(str(data_file)).get_summary()
            period = (pd.Timestamp(smry.start_time), pd.Timestamp(smry.end_time))
            if any(pd.isna(value) for value in period) or period[1] < period[0]:
                raise ValueError("invalid simulator source period")
            units = read_units(data_file, list(frame.columns))
            if not all(unit_is_valid(key, unit) for key, unit in units.items()):
                raise ValueError("summary vector units are unavailable")
            columns = set(frame.columns)
            if reference_index is not None:
                if not frame.index.equals(reference_index):
                    raise ValueError("member output dates differ")
                if columns != reference_columns:
                    raise ValueError("member vector sets differ")
                if units != reference_units:
                    raise ValueError("member vector units differ")
                if period != reference_period:
                    raise ValueError("member simulator source periods differ")
            else:
                reference_index, reference_columns, reference_units = frame.index, columns, units
                reference_period = period
        except (ValueError, TypeError) as error:
            issues.append({"realization": real, "issue": str(error)})
            continue
        frame = frame.rename_axis("DATE").reset_index()
        frame.insert(0, "REAL", real)
        frames.append(frame)
        sources.append({"realization": real, "file": str(unsmry), "sha256": sha256(unsmry)})

    found = {entry["realization"] for entry in sources}
    # A missing realization has no runpath folder. A member with a folder that fails a check is an issue.
    missing = list(ensemble.missing)
    if (issues or missing) and not args.allow_partial:
        for item in issues:
            print(f"ERROR: realization {item['realization']}: {item['issue']}", file=sys.stderr)
        if missing:
            print(f"ERROR: missing realizations (no runpath folder): {missing}", file=sys.stderr)
        print("Use --allow-partial to continue with the valid members.", file=sys.stderr)
        return 1
    if not frames:
        print("ERROR: no valid member", file=sys.stderr)
        return 1

    long = pd.concat(frames, ignore_index=True)
    # Every accepted member has the same validated vector/calendar/unit contract.
    dropped = []
    vectors = [c for c in long.columns if c not in ("REAL", "DATE")]
    # An optional key that only some members have is "member vector sets differ" above. It is not dropped.
    optional_dropped = [
        pattern
        for pattern in args.optional_keys
        if pattern not in args.keys and not any(fnmatch.fnmatchcase(key, pattern) for key in vectors)
    ]
    if optional_dropped:
        print(f"WARNING: optional keys not in any member, dropped: {optional_dropped}", file=sys.stderr)

    tidy = long.melt(id_vars=["REAL", "DATE"], var_name="KEY", value_name="VALUE")
    tidy["VALUE"] = pd.to_numeric(tidy["VALUE"], errors="raise")
    grouped = tidy.groupby(["DATE", "KEY"])["VALUE"]
    stats = grouped.agg(COUNT=lambda s: int(s.notna().sum()), MEAN="mean").reset_index()
    for name, quantile in (("P10", 0.10), ("P50", 0.50), ("P90", 0.90)):
        stats[name] = grouped.quantile(quantile).to_numpy()

    if not (stats["COUNT"] == len(found)).all() or not np.isfinite(stats.select_dtypes("number")).all().all():
        print("ERROR: incomplete or nonfinite derived statistics", file=sys.stderr)
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    long.to_csv(outdir / "summary_realizations.csv", index=False)
    stats.to_csv(outdir / "summary_stats.csv", index=False)
    manifest = {
        "runpath_glob": args.runpaths,
        "keys_requested": args.keys,
        "optional_keys_requested": args.optional_keys,
        "optional_keys_dropped": optional_dropped,
        "vectors_returned": vectors,
        "vectors_dropped_not_in_every_member": dropped,
        "units": reference_units,
        "time_index": args.time_index,
        "source_period": [value.isoformat() for value in reference_period],
        "percentile_convention": "numerical",
        "realizations_used": sorted(found),
        "expected": sorted(expected) if expected is not None else None,
        "missing_realizations": missing,
        "extra_realizations_not_used": extra,
        "issues": issues,
        "res2df_version": getattr(sys.modules.get("res2df"), "__version__", None),
        "pandas_version": pd.__version__,
        "sources": sources,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {outdir}: {len(found)} realizations, {len(vectors)} vectors, {len(issues)} issues")
    return 1 if (issues or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
