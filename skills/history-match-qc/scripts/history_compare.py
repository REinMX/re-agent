#!/usr/bin/env python3
"""Compare DATE CSV summary vectors with their H counterparts; never fill gaps."""

import argparse
import csv
import json
import math
import sys
from datetime import date
from pathlib import Path


def compare(path, tolerance):
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    with path.open(newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        if "DATE" not in fields or len(fields) != len(set(fields)):
            raise ValueError("unique columns and DATE are required")
        rows = list(reader)
    dates = [date.fromisoformat(row["DATE"]) for row in rows]
    if any(b <= a for a, b in zip(dates, dates[1:], strict=False)):
        raise ValueError("DATE must be strictly increasing")
    result = dict(
        vectors=[],
        unpaired=[],
        tolerance=tolerance,
        normalisation="RMS residual / RMS history over finite paired samples; zero history scale gives null normalised RMS. Divergence uses the same scale.",
    )
    for vector in fields:
        base, colon, name = vector.partition(":")
        if vector == "DATE" or base.endswith("H") or not base.startswith(("W", "G", "F")):
            continue
        history = base + "H" + (colon + name if colon else "")
        if history not in fields:
            result["unpaired"].append(vector)
            continue
        samples = []
        points = []
        for row in rows:
            values = []
            for key in (vector, history):
                value = row[key]
                number = float(value) if value and value.strip() else math.nan
                values.append(number if math.isfinite(number) else math.nan)
            sim, hist = values
            points.append(
                dict(
                    date=row["DATE"],
                    simulated=sim if math.isfinite(sim) else None,
                    history=hist if math.isfinite(hist) else None,
                )
            )
            if all(math.isfinite(v) for v in values):
                samples.append((row["DATE"], sim, hist))
        scale = math.sqrt(sum(h * h for _, _, h in samples) / len(samples)) if samples else None
        rms = math.sqrt(sum((s - h) ** 2 for _, s, h in samples) / len(samples)) if samples else None
        first = next((d for d, s, h in samples if abs(s - h) > tolerance * scale), None) if samples else None
        result["vectors"].append(
            dict(
                vector=vector,
                history_vector=history,
                samples=len(samples),
                missing_samples=len(rows) - len(samples),
                history_rms=scale,
                rms_error=rms,
                normalised_rms=rms / scale if scale else None,
                first_divergence_date=first,
                points=points,
            )
        )
    if not result["vectors"]:
        raise ValueError("no simulated/history vector pairs found")
    return result


def markdown(result):
    lines = [
        "# History comparison",
        result["normalisation"],
        "Units: inherited from CSV; simulated and history values must have the same units.",
        "| Vector | Samples | Missing | Normalised RMS | First divergence |",
        "|---|---:|---:|---:|---|",
    ]
    for row in result["vectors"]:
        lines.append(
            f"| {row['vector']} | {row['samples']} | {row['missing_samples']} | {row['normalised_rms']} | {row['first_divergence_date']} |"
        )
    return (
        "\n".join(lines)
        + f"\n\nUnpaired vectors: {result['unpaired']}\nNo control mode is inferred from summary vectors.\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = compare(args.csv, args.tolerance)
        args.outdir.mkdir(parents=True, exist_ok=False)
        (args.outdir / "report.json").write_text(json.dumps(result, indent=2, allow_nan=False))
        (args.outdir / "report.md").write_text(markdown(result))
    except (OSError, ValueError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
