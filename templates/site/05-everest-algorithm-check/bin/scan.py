#!/usr/bin/env python3
"""Brute-force reference for the two-control case: the mean NPV on a grid of the two injection rates.

  scan.py --out <scan folder> [--case case.yml] [--posterior-link posterior_link.yml] [--workers 3]

It runs the same forward-model steps as the Everest configs (prepare, schedule, simulate, economics) for each grid
point and each realization, so the Everest optimum can be checked against it. A finished point (an npv file)
is reused. Writes <out>/scan.csv and prints the best grid point. Cost: len(rates)^2 x realizations simulations.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]


def grid(case: dict, realizations: list[int]) -> list[tuple[float, float, int]]:
    rates = [float(r) for r in case["scan"]["rates"]]
    return list(itertools.product(rates, rates, realizations[: int(case["scan"]["realizations"])]))


def table(points, npv) -> dict[tuple[float, float], dict[int, float]]:
    out: dict[tuple[float, float], dict[int, float]] = {}
    for (a, b, real), value in zip(points, npv, strict=True):
        out.setdefault((a, b), {})[real] = value
    return out


def write_csv(path: Path, wells: list[str], values: dict) -> tuple[tuple[float, float], float]:
    reals = sorted(next(iter(values.values())))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(wells + [f"npv_r{r}" for r in reals] + ["npv_mean"])
        for key, vals in sorted(values.items()):
            w.writerow(
                [*key] + [round(vals[r], 3) for r in reals] + [round(sum(vals.values()) / len(vals), 3)]
            )
    best = max(values, key=lambda k: sum(values[k].values()))
    return best, sum(values[best].values()) / len(values[best])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--case", type=Path, default=HERE / "case.yml")
    ap.add_argument("--posterior-link", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args(argv)
    case_file = args.case.resolve()
    case = yaml.safe_load(case_file.read_text(encoding="utf-8"))
    if case.get("jobs", "custom") != "custom":
        ap.error("the reference scan requires jobs: custom; use the same NPV definition in Everest")
    wells = list(case["wells"]["injectors"])
    if len(wells) != 2:
        raise SystemExit(f"the scan needs exactly two injectors, case.yml has {len(wells)}")
    posterior, realizations = case["posterior"], [int(r) for r in case["everest"]["realizations"]]
    if args.posterior_link:
        snippet = yaml.safe_load(args.posterior_link.read_text(encoding="utf-8"))
        posterior = next(d["source"] for d in snippet["install_data"] if d["target"] == "posterior")
        realizations = [int(r) for r in snippet["model"]["realizations"]]
    job = (case_file.parent / case["scan"]["job"]).resolve()
    out = args.out.resolve()

    def run_point(a: float, b: float, real: int) -> float:
        d = (
            out / f"{wells[0]}_{a!r}_{wells[1]}_{b!r}" / f"r{real}"
        )  # lossless: distinct rates never share a folder
        if (d / "npv").exists():
            return float((d / "npv").read_text())
        d.mkdir(parents=True, exist_ok=True)
        (d / "inj.json").write_text(json.dumps({wells[0]: a, wells[1]: b}))
        source = posterior.replace("<REALIZATION_ID>", str(real))
        for step in (["prepare", source], ["schedule"], ["simulate"], ["economics"]):
            command = [sys.executable, str(job), *step, "--case", str(case_file)]
            result = subprocess.run(command, cwd=d, capture_output=True, text=True, check=False)
            (d / f"{step[0]}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise SystemExit(f"{d}: step {step[0]} failed with exit {result.returncode}")
        return float((d / "npv").read_text())

    points = grid(case, realizations)
    with ThreadPoolExecutor(args.workers) as pool:
        npv = list(pool.map(lambda p: run_point(*p), points))
    best, mean = write_csv(out / "scan.csv", wells, table(points, npv))
    print(
        f"best grid point {dict(zip(wells, best, strict=True))} mean npv {mean:.2f} over {len(points) // len(table(points, npv))} realizations"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
