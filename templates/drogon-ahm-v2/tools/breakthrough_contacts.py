#!/usr/bin/env python
"""Simulated breakthrough dates and contact parameters versus truth for completed ES runs.

Reads each member's UNSMRY and parameters.txt only. Breakthrough is the first summary
date where WWCT reaches the observation threshold, so the resolution is the report step.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jobs"))
from drogon_v2_lib import read_params  # noqa: E402

BT_LINE = re.compile(
    r"BREAKTHROUGH_OBSERVATION\s+(\S+)\s*\{[^}]*?KEY\s*=\s*WWCT:(\S+?)\s*;"
    r"[^}]*?DATE\s*=\s*(\d{4}-\d{2}-\d{2})\s*;[^}]*?THRESHOLD\s*=\s*([0-9.eE+-]+)\s*;"
)
CONTACTS = ("OWC_CENTRAL", "OWC_FLANK")


def parse_breakthrough_obs(text: str) -> dict[str, tuple[date, float]]:
    """{well: (truth date, threshold)} from BREAKTHROUGH_OBSERVATION lines."""
    out = {}
    for _, well, day, threshold in BT_LINE.findall(text):
        out[well] = (date.fromisoformat(day), float(threshold))
    if not out:
        raise ValueError("no BREAKTHROUGH_OBSERVATION with KEY WWCT, DATE and THRESHOLD")
    return out


def first_crossing(dates, values, threshold: float):
    """First date with value >= threshold, or None when it never crosses."""
    values = np.asarray(values, dtype=float)
    hits = np.flatnonzero(values >= threshold)
    return dates[hits[0]] if len(hits) else None


def summarize(errors_days: list[int], never: int) -> dict:
    e = np.asarray(errors_days, dtype=float)
    return dict(
        n_breakthrough=len(e),
        never=never,
        median_error_days=float(np.median(e)) if len(e) else None,
        mean_abs_error_days=float(np.mean(np.abs(e))) if len(e) else None,
        within_30_days=int(np.sum(np.abs(e) <= 30)),
    )


def spread(values, truth: float) -> dict:
    v = np.asarray(values, dtype=float)
    lo, hi = np.percentile(v, [5, 95])
    return dict(
        mean=float(v.mean()),
        sd=float(v.std(ddof=1)),
        p5=float(lo),
        p95=float(hi),
        truth=truth,
        truth_inside=bool(lo <= truth <= hi),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--setups", nargs="+", default=["before", "after"])
    parser.add_argument("--bt-obs", type=Path, required=True, help="obs file with breakthrough obs")
    parser.add_argument("--truth-params", type=Path, required=True)
    parser.add_argument("--n", type=int, default=16)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        from resdata.summary import Summary
    except ImportError:
        print("resdata is required to read UNSMRY files", file=sys.stderr)
        return 2
    obs = parse_breakthrough_obs(args.bt_obs.read_text())
    truth = read_params(args.truth_params)["DROGON"]
    report = dict(definition="simulated first WWCT crossing minus truth date, days", wells=sorted(obs))
    for setup in args.setups:
        report[setup] = {}
        for it in (0, 1):
            errors = {w: [] for w in obs}
            never = dict.fromkeys(obs, 0)
            contacts = {k: [] for k in CONTACTS}
            for i in range(args.n):
                member = args.scratch / setup / f"realization-{i}" / f"iter-{it}"
                smry = sorted((member / "model").glob("*.UNSMRY"))
                if len(smry) != 1:
                    raise ValueError(f"{member}: expected one UNSMRY, found {len(smry)}")
                summary = Summary(str(smry[0].with_suffix("")))
                dates = [d.date() for d in summary.dates]
                for well, (day, threshold) in obs.items():
                    hit = first_crossing(dates, summary.numpy_vector(f"WWCT:{well}"), threshold)
                    if hit is None:
                        never[well] += 1
                    else:
                        errors[well].append((hit - day).days)
                params = read_params(member / "parameters.txt")["DROGON"]
                for k in CONTACTS:
                    contacts[k].append(params[k])
            report[setup][f"iter-{it}"] = dict(
                breakthrough={w: summarize(errors[w], never[w]) for w in obs},
                contacts={k: spread(v, truth[k]) for k, v in contacts.items()},
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
