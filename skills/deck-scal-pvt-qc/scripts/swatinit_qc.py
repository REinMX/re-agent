#!/usr/bin/env python3
"""Summarize subscript SWATINIT QC at report step zero without writing cell CSVs."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys
import time
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / "stage-contract/scripts"))
from runpath_lib import realization_number  # noqa: E402

METRICS = ("hc_below_fwl_pct", "swl_trunc_pct", "hc_pv_change_pct")


def load_backend():
    # Set these before subscript imports matplotlib or a Qt backend.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MPLBACKEND"] = "Agg"
    try:
        from res2df import ResdataFiles
        from subscript.check_swatinit import check_swatinit
    except ImportError as exc:
        raise ValueError("subscript check_swatinit and its dependencies must be installed") from exc
    return check_swatinit, ResdataFiles


def analyze_case(case: Path):
    api, factory = load_backend()
    files = factory(str(case))
    deck = files.get_deck()
    # A deck without SWATINIT may still have it in INIT (binary imports).
    has_swatinit = "SWATINIT" in deck
    if not has_swatinit:
        try:
            has_swatinit = any(h[0] == "SWATINIT" for h in files.get_initfile().headers)
        except FileNotFoundError:
            pass
    if not has_swatinit:
        return None, "no SWATINIT in deck or INIT"
    files.get_initfile()
    restart = files.get_rstfile()
    if 0 not in restart.report_steps:
        raise ValueError("UNRST must contain report step 0")
    return api.make_qc_gridframe(files), None


def summarize(frame) -> dict:
    import numpy as np

    required = {"PORV", "SWATINIT", "SWAT", "QC_FLAG", "EQLNUM", "SATNUM", "PC_SCALING"}
    if missing := required - set(frame.columns):
        raise ValueError(f"missing QC columns: {sorted(missing)}")
    if frame.empty or not np.isfinite(frame[["PORV", "SWATINIT", "SWAT"]].to_numpy()).all():
        raise ValueError("empty or non-finite saturation/volume data")
    if (frame["PORV"] < 0).any() or not (
        (frame[["SWATINIT", "SWAT"]] >= 0) & (frame[["SWATINIT", "SWAT"]] <= 1)
    ).all().all():
        raise ValueError("invalid pore volume or saturation")
    if frame[["QC_FLAG", "EQLNUM", "SATNUM"]].isna().any().any():
        raise ValueError("missing QC flag or region number")
    hcpv = float(((1 - frame["SWATINIT"]) * frame["PORV"]).sum())
    if hcpv <= 0:
        raise ValueError("SWATINIT hydrocarbon pore volume must be positive")
    delta = (frame["SWATINIT"] - frame["SWAT"]) * frame["PORV"]
    maxima = {}
    for key in ("PPCWMAX", "PPCW", "PC_SCALING"):
        if key not in frame or frame[key].isna().all():
            if key == "PC_SCALING":
                raise ValueError("no finite PC_SCALING values")
            maxima[key] = None
            continue
        if np.isinf(frame[key].to_numpy()).any():
            raise ValueError(f"infinite {key} values")
        value = float(frame[key].max())
        regions = frame.loc[frame[key] == value, ["EQLNUM", "SATNUM"]].drop_duplicates()
        maxima[key] = {"value": value, "regions": regions.astype(int).to_dict("records")}
    return {
        "cells": len(frame),
        "swatinit_hcpv": hcpv,
        "hc_pv_change_pct": float(delta.sum() / hcpv * 100),
        "hc_below_fwl_pct": float(delta[frame["QC_FLAG"] == "HC_BELOW_FWL"].sum() / hcpv * 100),
        "swl_trunc_pct": float(delta[frame["QC_FLAG"] == "SWL_TRUNC"].sum() / hcpv * 100),
        "pc_scaling_max": maxima["PC_SCALING"]["value"],
        "maxima": maxima,
        "flag_counts": {str(k): int(v) for k, v in frame["QC_FLAG"].value_counts(dropna=False).items()},
    }


def exceeded(summary: dict, hc: float, swl: float, pc: float) -> list[str]:
    limits = {"hc_below_fwl_pct": hc, "swl_trunc_pct": swl, "pc_scaling_max": pc}
    return [key for key, limit in limits.items() if abs(summary[key]) > limit]


def contact_parameters(case: Path, params: str | None, names: list[str] | None) -> tuple[dict, str | None]:
    candidates = (
        [case.parent / params]
        if params
        else [case.parent / "parameters.txt", case.parent.parent / "parameters.txt"]
    )
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        if params:
            raise ValueError(f"parameters file not found for {case}: {params}")
        return {}, "no parameters.txt; contact correlations not checked"
    values = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        name = parts[0]
        selected = (
            name in names or name.split(":")[-1] in names
            if names
            else any(token in name.upper() for token in ("OWC", "GOC", "FWL", "GWC"))
        )
        if not selected:
            continue
        if len(parts) != 2 or name in values:
            raise ValueError(f"invalid or duplicate contact parameter in {path}: {name}")
        values[name] = float(parts[1])
        if not math.isfinite(values[name]):
            raise ValueError(f"non-finite contact parameter: {name}")
    if names and any(not any(key == n or key.split(":")[-1] == n for key in values) for n in names):
        raise ValueError(f"requested contact parameter missing from {path}")
    return values, None if values else "no contact parameters; correlations not checked"


def correlations(rows: list[dict]) -> list[dict]:
    import pandas as pd

    valid = [r for r in rows if r["status"] in ("ok", "thresholds exceeded")]
    names = sorted({name for r in valid for name in r["params"]})
    result = []
    for name in names:
        for metric in METRICS:
            pairs = [(r["params"][name], r[metric]) for r in valid if name in r["params"]]
            ranks = pd.DataFrame(pairs, columns=["parameter", "volume"]).rank(method="average")
            rho = None
            if len(pairs) >= 3 and ranks.nunique().min() > 1:
                rho = float(ranks["parameter"].corr(ranks["volume"]))
            finding = (
                "contact parameter acts as a STOIIP knob"
                if metric == "hc_below_fwl_pct" and rho is not None and abs(rho) >= 0.7
                else None
            )
            result.append(
                {"parameter": name, "metric": metric, "n": len(pairs), "spearman": rho, "finding": finding}
            )
    return result


def write_reports(out: Path, rows: list[dict], limits: dict) -> dict:
    applicable = [r for r in rows if r["status"] != "not applicable"]
    status = (
        "thresholds exceeded"
        if any(r["exceeded"] for r in applicable)
        else "ok"
        if applicable
        else "not applicable"
    )
    corr = correlations(rows)
    report = {
        "status": status,
        "basis": "Signed HC change as percent of SWATINIT HCPV; report step 0. Thresholds use magnitudes.",
        "limits": limits,
        "realizations": rows,
        "correlations": corr,
    }
    (out / "swatinit_qc.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    fields = [
        "realization",
        "case",
        "status",
        "seconds",
        "cells",
        *METRICS,
        "pc_scaling_max",
        "maxima",
        "flag_counts",
        "params",
        "exceeded",
        "note",
    ]
    with (out / "swatinit_qc.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    lines = [
        f"# SWATINIT QC: {status}",
        "",
        report["basis"],
        "",
        f"Limits: {json.dumps(limits)}",
        "",
        f"Realizations checked: {len(rows)}; applicable: {len(applicable)}.",
        "",
        "| Realization | Status | HC change % | HC_BELOW_FWL % | SWL_TRUNC % | PC scaling max |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        values = [
            str(row.get(k, "n/a"))
            for k in (
                "realization",
                "status",
                "hc_pv_change_pct",
                "hc_below_fwl_pct",
                "swl_trunc_pct",
                "pc_scaling_max",
            )
        ]
        lines.append("| " + " | ".join(values) + " |")
    for row in rows:
        lines.append("")
        lines.append(f"{row['case']}: limits exceeded {row['exceeded']}; {row.get('note') or ''}")
        if "maxima" in row:
            lines.append(f"Maxima and EQLNUM/SATNUM: {json.dumps(row['maxima'])}")
            lines.append(f"Cell flag counts: {json.dumps(row['flag_counts'])}")
    lines.extend(
        [
            "",
            "Contact correlations are screening evidence, not proof of causation. At least three pairs are required.",
            "",
        ]
    )
    for item in corr:
        lines.append(
            f"- {item['parameter']} / {item['metric']}: Spearman={item['spearman']}, n={item['n']}; {item['finding'] or 'no knob flag'}"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--ensemble-glob")
    parser.add_argument("--params", help="Absolute path or path relative to each case directory")
    parser.add_argument("--contact-params", nargs="+")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--keep-cells", action="store_true")
    parser.add_argument("--max-hc-below-fwl", type=float, default=1.0)
    parser.add_argument("--max-swl-trunc", type=float, default=1.0)
    parser.add_argument("--max-pc-scaling", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        if not args.case and not args.ensemble_glob:
            raise ValueError("give --case or --ensemble-glob")
        limits = {
            "hc_below_fwl_pct": args.max_hc_below_fwl,
            "swl_trunc_pct": args.max_swl_trunc,
            "pc_scaling_max": args.max_pc_scaling,
        }
        if any(not math.isfinite(v) or v < 0 for v in limits.values()):
            raise ValueError("thresholds must be finite and nonnegative")
        if args.ensemble_glob:
            pattern = args.ensemble_glob
            cases = sorted(
                Path(p).resolve()
                for p in glob.glob(pattern if pattern.endswith(".DATA") else pattern + ".DATA")
            )
            if not cases:
                raise ValueError("ensemble glob matched no DATA files")
            reals = [realization_number(p) for p in cases]
            if None in reals or len(set(reals)) != len(reals):
                raise ValueError("ensemble requires one case per unique realization-N")
        else:
            case = args.case
            cases = [(case if case.suffix.upper() == ".DATA" else Path(str(case) + ".DATA")).resolve()]
        output = args.out.resolve()
        for case in cases:
            if not case.is_file():
                raise ValueError(f"DATA file not found: {case}")
            tree = next((p for p in case.parents if p.name.startswith("iter-")), case.parent)
            if output == tree or tree in output.parents or output in case.parents:
                raise ValueError("output overlaps an input tree")
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ValueError("output must be a new or empty directory")
        rows = []
        frames = []
        for case in cases:
            start = time.monotonic()
            frame, note = analyze_case(case)
            row = {
                "case": str(case),
                "realization": realization_number(case),
                "status": "not applicable",
                "note": note,
                "params": {},
                "exceeded": [],
            }
            if frame is not None:
                row.update(summarize(frame))
                row["params"], row["note"] = contact_parameters(case, args.params, args.contact_params)
                row["exceeded"] = exceeded(row, *limits.values())
                row["status"] = "thresholds exceeded" if row["exceeded"] else "ok"
                if args.keep_cells:
                    frames.append((len(rows), frame))
            row["seconds"] = round(time.monotonic() - start, 3)
            rows.append(row)
        output.mkdir(parents=True, exist_ok=True)
        for index, frame in frames:
            frame.to_csv(output / f"cells-{index}.csv", index=False)
        report = write_reports(output, rows, limits)
        print(f"SWATINIT QC: {report['status']}; {len(rows)} realizations; {output / 'report.md'}")
        return 1 if report["status"] == "thresholds exceeded" else 0
    except (OSError, ValueError, KeyError, ImportError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
