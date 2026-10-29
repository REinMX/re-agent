#!/usr/bin/env python3
"""Build SUMMARY_OBSERVATION blocks from a reference case (field history or a synthetic truth).

  make_obs.py <reference .DATA> --out obs/history.obs [--spec obs/obs_spec.yml]

A point is skipped when the well has no flow at that date: the BHP or the rate of a shut well
carries no information about the reservoir. With noise_seed set, Gaussian noise with the stated
error is added (synthetic truth only). Water cut stays in [0, 1]. Needs res2df (Komodo).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SPEC = HERE.parent / "obs" / "obs_spec.yml"


def error(spec: dict, vector: str, value: float) -> float:
    rule = spec["errors"].get(vector.split(":")[0])
    if rule is None:
        raise SystemExit(f"no error rule for {vector} in errors")
    if "abs" in rule:
        return float(rule["abs"])
    return max(float(rule["rel"]) * abs(value), float(rule["min"]))


def build(spec: dict, value) -> tuple[list[str], int]:
    """Blocks and the count of skipped points. value(key, date) gives the reference value."""
    rng = random.Random(spec["noise_seed"]) if spec.get("noise_seed") is not None else None
    suffix = spec.get("value_suffix") or ""
    wells = [(w, spec["producer_vectors"], spec["producer_flow"]) for w in spec["producers"]]
    wells += [(w, spec["injector_vectors"], spec["injector_flow"]) for w in spec["injectors"]]
    blocks, skipped = [], 0
    for well, vectors, flow in wells:
        for kind in vectors:
            vector = f"{kind}:{well}"
            for day in map(str, spec["dates"]):
                if sum(value(f"{f}{suffix}:{well}", day) for f in flow) <= 0:
                    skipped += 1
                    continue
                reference = value(f"{kind}{suffix}:{well}", day)
                err = error(spec, vector, reference)
                obs = reference + (rng.gauss(0.0, err) if rng else 0.0)
                if kind == "WWCT":
                    obs = min(max(obs, 0.0), 1.0)
                name = f"{kind}_{well}_{day.replace('-', '')}"
                body = f"VALUE = {obs:.4f}; ERROR = {err:.4f}; DATE = {day}; KEY = {vector};"
                blocks.append(f"SUMMARY_OBSERVATION {name} " + "{ " + body + " };")
    return blocks, skipped


def summary_lookup(data_file: Path, spec: dict):
    from res2df import ResdataFiles, summary

    suffix = spec.get("value_suffix") or ""
    keys = sorted(
        {
            f"{k}{suffix}:{w}"
            for w in spec["producers"]
            for k in spec["producer_vectors"] + spec["producer_flow"]
        }
        | {
            f"{k}{suffix}:{w}"
            for w in spec["injectors"]
            for k in spec["injector_vectors"] + spec["injector_flow"]
        }
    )
    df = summary.df(ResdataFiles(str(data_file)), column_keys=keys, time_index="raw")
    df.index = df.index.normalize()
    missing = sorted(set(keys) - set(df.columns))
    if missing:
        raise SystemExit(f"the reference case does not write: {', '.join(missing)}")

    def value(key: str, day: str) -> float:
        rows = df.loc[df.index == day, key]
        if rows.empty:
            raise SystemExit(f"{day} is not a report date of the reference case")
        return float(rows.iloc[-1])

    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "reference", type=Path, help="the .DATA file of the reference case, with its summary files"
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--spec", type=Path, default=SPEC)
    args = ap.parse_args(argv)
    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8"))
    blocks, skipped = build(spec, summary_lookup(args.reference, spec))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(
        f"wrote {len(blocks)} observations to {args.out}; skipped {skipped} points where the well had no flow"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
