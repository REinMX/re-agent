#!/usr/bin/env python3
"""Compute P10/P50/P90 ensemble bands from profiles in the contract convention.

Convention from contract p10_p90 (no default):
  numerical  : P10 = 10th percentile (low), P90 = 90th percentile (high)
  exceedance : P10 = 90th percentile (upside), P90 = 10th percentile
State this convention in every report. Never invent zeros for missing vectors.

Approach (documented):
  1) End-of-sim table: last DATE per REAL per KEY, then P10/P50/P90 across reals.
  2) Optional time bands: at each DATE, P10/P50/P90 across reals (monthly profiles).

Missing-vector policy:
  - Default: require every name in contract required_summary (well families match
    WOPR:WELL etc.). If any required name is absent -> write missing_vectors.json
    and exit 2. Do not write band CSVs that pretend the vector exists.
  - --vectors FOPT,FWPT,...: check only the listed names (progress path while
    the SUMMARY section is being extended).

Usage:
    ensemble_bands.py --contract /path/to/site.contract.yml
    ensemble_bands.py --contract ... --vectors FOPT,FWPT,FOPR,FWPR,WOPR,WWPR,WBHP,WWCT

Exit: 0 ok, 2 missing required vectors or bad input, 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

CONVENTION_NOTE = {
    "numerical": "numerical percentiles: P10 = 10th percentile of the ensemble (low), P50 = median, "
    "P90 = 90th percentile (high). Not exceedance.",
    "exceedance": "exceedance percentiles: P10 = 90th percentile of the ensemble (upside, 10% chance "
    "to exceed), P50 = median, P90 = 10th percentile (downside).",
}


def mnemonic_family(key: str) -> str:
    return key.split(":", 1)[0]


def families_in_data(keys: list[str]) -> set[str]:
    return {mnemonic_family(k) for k in keys}


def resolve_keys_for_name(name: str, available: list[str]) -> list[str]:
    """Map a contract/required name to profile KEY columns."""
    if name in available:
        return [name]
    matches = [k for k in available if mnemonic_family(k) == name]
    return matches


def numerical_percentiles(values: np.ndarray) -> dict[str, float]:
    """P10/P50/P90 as numerical percentiles (low=P10, high=P90)."""
    return band_percentiles(values, "numerical")


def _write_parquet(df: pd.DataFrame, path: Path, report: dict) -> None:
    """Parquet copy next to the CSV. The CSV is the deliverable; skip parquet without an engine."""
    try:
        df.to_parquet(path, index=False)
    except ImportError as exc:
        report.setdefault("notes", []).append(f"{path.name} not written (no parquet engine: {exc})")


def band_percentiles(values: np.ndarray, convention: str) -> dict[str, float]:
    """P10/P50/P90 in the contract convention. NaN is dropped, never set to zero."""
    clean = values[np.isfinite(values)]
    if clean.size == 0:
        return {"P10": float("nan"), "P50": float("nan"), "P90": float("nan"), "COUNT": 0}
    lo, mid, hi = (float(np.percentile(clean, q)) for q in (10, 50, 90))
    if convention == "exceedance":
        lo, hi = hi, lo
    return {"P10": lo, "P50": mid, "P90": hi, "COUNT": int(clean.size)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_contract_args(parser)
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=None,
        help="Directory with profiles_long.parquet/csv (default: deliverables/profiles)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Default: deliverables/bands",
    )
    parser.add_argument(
        "--vectors",
        default=None,
        help=(
            "Comma-separated vector/family names to require and band. "
            "Default: contract vectors.required_summary (fails if any is missing)."
        ),
    )
    parser.add_argument(
        "--no-time-bands",
        action="store_true",
        help="Skip per-DATE band file; write end-of-sim table only",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        p10_p90 = contract.p10_p90
        deliverables = contract.path("deliverables")
        _sub, deck = contract.eclbase()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    for note in contract.warnings:
        sys.stderr.write(f"note: contract: {note}\n")
    profiles_dir = Path(args.profiles_dir or (deliverables / "profiles"))
    outdir = Path(args.outdir or (deliverables / "bands"))

    parquet = profiles_dir / "profiles_long.parquet"
    csv = profiles_dir / "profiles_long.csv"
    if parquet.is_file():
        tidy = pd.read_parquet(parquet)
    elif csv.is_file():
        tidy = pd.read_csv(csv, parse_dates=["DATE"])
    else:
        sys.stderr.write(
            f"error: profiles not found under {profiles_dir} "
            "(need profiles_long.parquet or .csv). Run ensemble-profiles first.\n"
        )
        return 2

    if tidy.empty or not {"REAL", "DATE", "KEY", "VALUE"}.issubset(tidy.columns):
        sys.stderr.write("error: profiles table missing REAL/DATE/KEY/VALUE columns\n")
        return 2

    tidy = tidy.copy()
    tidy["DATE"] = pd.to_datetime(tidy["DATE"])
    tidy["VALUE"] = pd.to_numeric(tidy["VALUE"], errors="coerce")
    available = sorted(tidy["KEY"].astype(str).unique())
    fams = families_in_data(available)

    if args.vectors:
        required = [v.strip() for v in args.vectors.split(",") if v.strip()]
        vector_mode = "explicit --vectors"
    else:
        required = contract.required_summary
        vector_mode = "contract required_summary"
        if not required:
            sys.stderr.write("error: contract required_summary empty\n")
            return 2

    missing: list[str] = []
    resolved: dict[str, list[str]] = {}
    for name in required:
        keys = resolve_keys_for_name(name, available)
        if not keys:
            missing.append(name)
        else:
            resolved[name] = keys

    outdir.mkdir(parents=True, exist_ok=True)
    missing_path = outdir / "missing_vectors.json"
    report = {
        "skill": "ensemble-bands",
        "contract": str(args.contract),
        "profiles_dir": str(profiles_dir),
        "p10_p90": p10_p90,
        "p10_p90_note": CONVENTION_NOTE[p10_p90],
        "vector_mode": vector_mode,
        "required": required,
        "available_keys": available,
        "available_families": sorted(fams),
        "resolved": resolved,
        "missing_vectors": missing,
        "approach": {
            "end_of_sim": "last DATE per REAL per KEY, then np.percentile 10/50/90",
            "time_bands": None if args.no_time_bands else "at each DATE, np.percentile 10/50/90 across REAL",
        },
    }

    if missing:
        missing_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (outdir / "README.md").write_text(
            "# Ensemble bands — FAILED (missing SUMMARY vectors)\n\n"
            f"- Convention requested: **{p10_p90}** P10/P50/P90\n"
            f"- Vector mode: {vector_mode}\n"
            f"- Missing (not filled with zero): {', '.join(missing)}\n"
            f"- See `missing_vectors.json`.\n"
            f"- Fix: extend the SUMMARY section of deck `{deck}` with the missing vectors "
            "(and re-run the ensemble), "
            "or call with `--vectors` listing only vectors that exist.\n",
            encoding="utf-8",
        )
        sys.stderr.write(f"error: missing required SUMMARY vectors ({vector_mode}): {', '.join(missing)}\n")
        sys.stderr.write(f"wrote {missing_path}\n")
        sys.stderr.write(
            "Refuse to invent zeros. Exit 2. Use --vectors FOPT,FWPT,... for the known-present subset.\n"
        )
        return 2

    # Flatten KEY list we will band
    keys_to_band = sorted({k for ks in resolved.values() for k in ks})
    subset = tidy[tidy["KEY"].isin(keys_to_band)].copy()

    # End-of-sim: last date per REAL, KEY
    subset = subset.sort_values(["REAL", "KEY", "DATE"])
    end = subset.groupby(["REAL", "KEY"], as_index=False).tail(1)
    end_rows: list[dict[str, Any]] = []
    for key, grp in end.groupby("KEY"):
        stats = band_percentiles(grp["VALUE"].to_numpy(dtype=float), p10_p90)
        end_rows.append(
            {
                "KEY": key,
                "FAMILY": mnemonic_family(str(key)),
                "METRIC": "end_of_sim",
                "DATE_MAX": str(grp["DATE"].max().date()),
                "MEAN": float(np.nanmean(grp["VALUE"].to_numpy(dtype=float))),
                **stats,
            }
        )
    end_df = pd.DataFrame(end_rows).sort_values(["FAMILY", "KEY"])
    end_csv = outdir / "bands_end_of_sim.csv"
    end_df.to_csv(end_csv, index=False)

    time_csv = None
    if not args.no_time_bands:
        time_rows: list[dict[str, Any]] = []
        for (date, key), grp in subset.groupby(["DATE", "KEY"]):
            stats = band_percentiles(grp["VALUE"].to_numpy(dtype=float), p10_p90)
            time_rows.append(
                {
                    "DATE": date,
                    "KEY": key,
                    "FAMILY": mnemonic_family(str(key)),
                    "MEAN": float(np.nanmean(grp["VALUE"].to_numpy(dtype=float))),
                    **stats,
                }
            )
        time_df = pd.DataFrame(time_rows).sort_values(["KEY", "DATE"])
        time_csv = outdir / "bands_time_series.csv"
        time_df.to_csv(time_csv, index=False)
        _write_parquet(time_df, outdir / "bands_time_series.parquet", report)

    _write_parquet(end_df, outdir / "bands_end_of_sim.parquet", report)

    report["outputs"] = {
        "bands_end_of_sim_csv": str(end_csv),
        "bands_end_of_sim_parquet": str(outdir / "bands_end_of_sim.parquet"),
        "bands_time_series_csv": str(time_csv) if time_csv else None,
        "bands_time_series_parquet": str(outdir / "bands_time_series.parquet") if time_csv else None,
    }
    report["n_keys_banded"] = len(keys_to_band)
    report["n_realizations"] = int(subset["REAL"].nunique())
    (outdir / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Clear any prior failure artifact when success
    if missing_path.is_file():
        missing_path.unlink()

    (outdir / "README.md").write_text(
        "# Ensemble bands\n\n"
        f"- **P10/P90 convention: {p10_p90}** ({CONVENTION_NOTE[p10_p90]})\n"
        f"- Vector mode: {vector_mode}\n"
        f"- Keys banded: {', '.join(keys_to_band)}\n"
        f"- Realizations: {report['n_realizations']}\n"
        "- Files: `bands_end_of_sim.csv` (primary), "
        "`bands_time_series.csv` (optional per-DATE bands).\n"
        "- Missing vector policy: fail loud; never fill with zero.\n",
        encoding="utf-8",
    )

    sys.stdout.write(
        f"Wrote {outdir}: {len(keys_to_band)} keys, {report['n_realizations']} reals, p10_p90={p10_p90}\n"
    )
    # Short end-of-sim preview for the contract headline vectors (default FOPT) when present.
    for head in contract.vectors("headline", ["FOPT"]):
        hit = end_df[end_df["KEY"] == head]
        if hit.empty:
            continue
        row = hit.iloc[0]
        sys.stdout.write(
            f"{head} end-of-sim {p10_p90}: P10={row['P10']:.4g} P50={row['P50']:.4g} "
            f"P90={row['P90']:.4g} COUNT={int(row['COUNT'])}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
