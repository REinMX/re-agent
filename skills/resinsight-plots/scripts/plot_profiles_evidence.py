#!/usr/bin/env python3
"""Matplotlib evidence plots from deliverables profiles/bands (fallback).

These PNGs are NOT ResInsight exports. Label every figure clearly.
Tables / scenario-qc decide pass/fail; plots are evidence only.

P10/P90: the contract convention (numerical: low=P10, high=P90) — stated on every figure.

Usage:
  plot_profiles_evidence.py --contract /path/to/site.contract.yml
  plot_profiles_evidence.py --contract ... --outdir .../deliverables/plots
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402


def read_table(path_parquet: Path, path_csv: Path) -> pd.DataFrame:
    if path_csv.is_file():
        return pd.read_csv(path_csv, parse_dates=["DATE"])
    if path_parquet.is_file():
        return pd.read_parquet(path_parquet)
    raise FileNotFoundError(f"missing {path_csv} and {path_parquet}")


def plot_band(
    bands: pd.DataFrame,
    key: str,
    out_path: Path,
    convention: str,
    count_hint: int | None,
) -> None:
    sub = bands[bands["KEY"] == key].sort_values("DATE")
    if sub.empty:
        raise ValueError(f"no band rows for KEY={key}")
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.fill_between(
        sub["DATE"],
        sub["P10"],
        sub["P90"],
        color="C0",
        alpha=0.25,
        label=f"P10–P90 ({convention})",
    )
    ax.plot(sub["DATE"], sub["P50"], color="C0", lw=2, label="P50")
    ax.plot(sub["DATE"], sub["MEAN"], color="C1", lw=1.5, ls="--", label="mean")
    n = int(sub["COUNT"].iloc[-1]) if "COUNT" in sub.columns else (count_hint or 0)
    ax.set_title(
        f"{key} ensemble bands — matplotlib evidence (NOT ResInsight)\n"
        f"P10/P90 convention: {convention} "
        f"({'low=P10, high=P90' if convention == 'numerical' else 'P10=upside, P90=downside'}); n={n}"
    )
    ax.set_xlabel("DATE")
    ax.set_ylabel(key)
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.text(
        0.01,
        0.01,
        "EVIDENCE ONLY — QC/tables gate pass/fail. Backend: matplotlib from deliverables/bands.",
        fontsize=8,
        color="dimgray",
    )
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_multi_real(
    profiles: pd.DataFrame,
    key: str,
    out_path: Path,
    reals: list[int],
    convention: str,
) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    for r in reals:
        sub = profiles[(profiles["REAL"] == r) & (profiles["KEY"] == key)].sort_values("DATE")
        if sub.empty:
            continue
        ax.plot(sub["DATE"], sub["VALUE"], lw=1.2, label=f"real-{r}")
    ax.set_title(
        f"{key} multi-real sample — matplotlib evidence (NOT ResInsight)\n"
        f"P10/P90 convention (for bands elsewhere): {convention}"
    )
    ax.set_xlabel("DATE")
    ax.set_ylabel(key)
    ax.legend(loc="best", fontsize=8, ncol=2)
    ax.grid(True, alpha=0.3)
    fig.text(
        0.01,
        0.01,
        "EVIDENCE ONLY — QC/tables gate pass/fail. Backend: matplotlib from deliverables/profiles.",
        fontsize=8,
        color="dimgray",
    )
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_contract_args(parser)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Default: <deliverables>/plots/evidence",
    )
    parser.add_argument(
        "--keys",
        default=None,
        help="Comma-separated KEY values to plot (default: contract vectors.plot_required + WOPR:<wells.plot_default>)",
    )
    parser.add_argument(
        "--sample-reals",
        default=None,
        help="Realization ids for multi-real sample overlays (default: up to 5 spread over the reals found)",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        convention = contract.p10_p90
        deliverables = contract.path("deliverables")
        required_keys = contract.plot_required()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    bands_dir = deliverables / "bands"
    profiles_dir = deliverables / "profiles"
    outdir = args.outdir or (deliverables / "plots" / "evidence")
    outdir.mkdir(parents=True, exist_ok=True)

    bands = read_table(
        bands_dir / "bands_time_series.parquet",
        bands_dir / "bands_time_series.csv",
    )
    # field + well profiles both have REAL,DATE,KEY,VALUE; profiles_long holds both
    if (profiles_dir / "field_profiles.csv").is_file() or (profiles_dir / "field_profiles.parquet").is_file():
        field = read_table(
            profiles_dir / "field_profiles.parquet",
            profiles_dir / "field_profiles.csv",
        )
        well = read_table(
            profiles_dir / "well_profiles.parquet",
            profiles_dir / "well_profiles.csv",
        )
        profiles = pd.concat([field, well], ignore_index=True)
    else:
        profiles = read_table(
            profiles_dir / "profiles_long.parquet",
            profiles_dir / "profiles_long.csv",
        )

    keys = [k.strip() for k in args.keys.split(",") if k.strip()] if args.keys else list(required_keys)
    if args.sample_reals:
        reals = [int(x) for x in args.sample_reals.split(",") if x.strip()]
    else:
        found = sorted(int(r) for r in profiles["REAL"].dropna().unique()) if "REAL" in profiles else []
        step = max(1, len(found) // 5) if found else 1
        reals = found[::step][:5]
    written: list[dict[str, Any]] = []
    missing: list[str] = []

    for key in keys:
        safe = key.replace(":", "_")
        band_path = outdir / f"evidence_{safe}_ensemble_bands.png"
        sample_path = outdir / f"evidence_{safe}_multi_real.png"
        try:
            plot_band(bands, key, band_path, convention, None)
            written.append(
                {
                    "key": key,
                    "path": str(band_path),
                    "backend": "matplotlib",
                    "kind": "ensemble_bands",
                    "p10_p90": convention,
                    "resinsight": False,
                }
            )
        except Exception as exc:  # noqa: BLE001
            missing.append(f"bands:{key}:{exc}")
            sys.stderr.write(f"warn: band plot failed for {key}: {exc}\n")

        try:
            plot_multi_real(profiles, key, sample_path, reals, convention)
            written.append(
                {
                    "key": key,
                    "path": str(sample_path),
                    "backend": "matplotlib",
                    "kind": "multi_real_sample",
                    "p10_p90": convention,
                    "resinsight": False,
                }
            )
        except Exception as exc:  # noqa: BLE001
            missing.append(f"sample:{key}:{exc}")
            sys.stderr.write(f"warn: sample plot failed for {key}: {exc}\n")

    manifest = {
        "skill": "resinsight-plots",
        "script": "plot_profiles_evidence.py",
        "backend": "matplotlib",
        "label": "NOT ResInsight — evidence plots from deliverables",
        "p10_p90": convention,
        "plots": written,
        "missing_or_failed": missing,
        "note": "Tables and scenario-qc gate pass/fail; plots are evidence only.",
    }
    (outdir / "manifest_evidence.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"outdir": str(outdir), "n_plots": len(written), "failed": missing}, indent=2))
    # Require a band plot for each contract plot_required vector (and the default well, when set)
    required_kinds = {(k, "ensemble_bands") for k in (keys if args.keys else required_keys)}
    have = {(p["key"], p["kind"]) for p in written}
    if not required_kinds.issubset(have):
        sys.stderr.write(f"error: missing required evidence plots; have={have} need={required_kinds}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
