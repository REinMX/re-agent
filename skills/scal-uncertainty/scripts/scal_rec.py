#!/usr/bin/env python3
"""Validate SCAL recommendations and generate simulator tables with pyscal."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


class RecommendationError(ValueError):
    """The recommendation has invalid or missing physical inputs."""


def recommendation_frame(rec: Path):
    from pyscal.factory import load_relperm_df

    try:
        frame = load_relperm_df(rec)
    except ValueError as error:
        raise RecommendationError(f"low/base/high recommendation: {error}") from error
    frame.columns = [str(column).upper() for column in frame.columns]
    if not {"SATNUM", "CASE"}.issubset(frame.columns):
        raise RecommendationError("SATNUM and CASE are required")
    frame["CASE"] = frame["CASE"].str.lower().str.strip()
    if frame.empty:
        raise RecommendationError("recommendation is empty")
    satnums = sorted(frame["SATNUM"].unique())
    if satnums != list(range(1, len(satnums) + 1)):
        raise RecommendationError("SATNUM must be consecutive integers starting at 1")
    for satnum, group in frame.groupby("SATNUM"):
        if sorted(group["CASE"].tolist()) != ["base", "high", "low"]:
            raise RecommendationError(f"SATNUM {satnum}: exactly one low/base/high row is required")
        for _, row in group.iterrows():
            where = f"SATNUM {satnum} {row['CASE']}"
            for column in (
                "SWL",
                "SWCR",
                "SORW",
                "SGCR",
                "SORG",
                "KRWEND",
                "KROWEND",
                "KROGEND",
                "KROEND",
                "KRGEND",
            ):
                if column in row and (
                    not math.isfinite(float(row[column])) or not 0 <= float(row[column]) <= 1
                ):
                    raise RecommendationError(f"{where}: {column} must be finite and in [0,1]")
            if {"SWL", "SWCR", "SORW"}.issubset(row.index):
                if not row["SWL"] <= row["SWCR"] < 1 - row["SORW"]:
                    raise RecommendationError(f"{where}: endpoints must satisfy SWL <= SWCR < 1-SORW")
            if {"SWL", "SGCR", "SORG"}.issubset(row.index):
                if row["SGCR"] + row["SORG"] + row["SWL"] >= 1:
                    raise RecommendationError(f"{where}: SGCR + SORG + SWL must be less than 1")
    return frame


def load_recommendation(rec: Path, delta_s: float = 0.01):
    from pyscal.factory import create_scal_recommendation_list

    if not math.isfinite(delta_s) or not 0 < delta_s <= 0.1:
        raise RecommendationError("delta-s must be in (0, 0.1]")
    return create_scal_recommendation_list(recommendation_frame(rec), h=delta_s)


def curve_tables(curves):
    for satnum, item in enumerate(curves.pyscal_list, 1):
        for keyword, phase in (("SWOF", item.wateroil), ("SGOF", item.gasoil)):
            if phase is not None:
                yield keyword, satnum, phase.table


def validate_curves(curves):
    import numpy as np

    tables = []
    for keyword, satnum, table in curve_tables(curves):
        saturation, wet, oil = ("SW", "KRW", "KROW") if keyword == "SWOF" else ("SG", "KRG", "KROG")
        for column in (saturation, wet, oil):
            values = table[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or np.any(values < -1e-9) or np.any(values > 1 + 1e-9):
                raise RecommendationError(f"{keyword} SATNUM {satnum}: {column} is outside [0,1]")
            difference = np.diff(values)
            if (column == oil and np.any(difference > 1e-9)) or (
                column != oil and np.any(difference < -1e-9)
            ):
                raise RecommendationError(f"{keyword} SATNUM {satnum}: {column} is not monotone")
        tables.append({"keyword": keyword, "satnum": satnum, "rows": len(table)})
    if not tables:
        raise RecommendationError("recommendation generated no curves")
    return tables


def check_recommendation(rec: Path, out: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    report = {"source": str(rec), "valid": False, "errors": [], "interpolations": []}
    figure, axis = plt.subplots()
    try:
        recommendation = load_recommendation(rec)
        for param in (-1, -0.5, 0, 0.5, 1):
            curves = recommendation.interpolate(param, h=0.01)
            tables = validate_curves(curves)
            report["interpolations"].append({"param": param, "tables": tables})
            for keyword, satnum, table in curve_tables(curves):
                saturation, wet, oil = ("SW", "KRW", "KROW") if keyword == "SWOF" else ("SG", "KRG", "KROG")
                for column in (wet, oil):
                    axis.plot(
                        table[saturation], table[column], label=f"{keyword} {satnum} {column} P={param}"
                    )
        report["valid"] = True
        axis.set(xlabel="Phase saturation", ylabel="Relative permeability", ylim=(0, 1))
        axis.legend(fontsize="xx-small", ncols=2)
        figure.savefig(out / "curves.png", dpi=150, bbox_inches="tight")
    except (ValueError, TypeError, KeyError, AssertionError) as error:
        report["errors"].append(str(error))
    finally:
        plt.close(figure)
    (out / "check.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def generate_tables(rec: Path, params: list[float], delta_s: float, out: Path):
    if not params or any(not math.isfinite(param) or abs(param) > 1 for param in params):
        raise RecommendationError("parameters must be finite and in [-1,1]")
    recommendation = load_recommendation(rec, delta_s)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    qc_script = Path(__file__).resolve().parents[2] / "deck-scal-pvt-qc/scripts/deck_qc.py"
    for index, param in enumerate(params):
        curves = recommendation.interpolate(param, h=delta_s)
        tables = validate_curves(curves)
        include = (out / f"relperm_{index}.inc").resolve()
        text = curves.SWOF() if any(table["keyword"] == "SWOF" for table in tables) else ""
        if any(table["keyword"] == "SGOF" for table in tables):
            text += "\n" + curves.SGOF()
        include.write_text(text)
        qc = subprocess.run(
            [sys.executable, str(qc_script), "--include", str(include), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            findings = json.loads(qc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"deck_qc could not check {include}: {qc.stderr or qc.stdout}") from None
        if qc.returncode not in (0, 1):
            raise RuntimeError(f"deck_qc failed: {qc.stderr or qc.stdout}")
        results.append(
            {
                "param": param,
                "include": str(include),
                "tables": tables,
                "qc": {"returncode": qc.returncode, "findings": findings},
            }
        )
    (out / "tables.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "tables"):
        child = subparsers.add_parser(command)
        child.add_argument("rec", type=Path)
        child.add_argument("--out", type=Path, default=Path("scal-check"))
        if command == "tables":
            child.add_argument("--param", default="-1,0,1")
            child.add_argument("--delta-s", type=float, default=0.01)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--param" in arguments:
        index = arguments.index("--param")
        if index + 1 < len(arguments) and arguments[index + 1].startswith("-"):
            arguments[index : index + 2] = [f"--param={arguments[index + 1]}"]
    args = parser.parse_args(arguments)
    try:
        if args.command == "check":
            report = check_recommendation(args.rec, args.out)
            print(json.dumps(report, indent=2))
            return 0 if report["valid"] else 1
        results = generate_tables(
            args.rec, [float(value) for value in args.param.split(",")], args.delta_s, args.out
        )
        print(json.dumps(results, indent=2))
        return int(any(result["qc"]["returncode"] for result in results))
    except (ImportError, OSError, ValueError, TypeError, KeyError, AssertionError, RuntimeError) as error:
        print(f"SCAL recommendation error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
