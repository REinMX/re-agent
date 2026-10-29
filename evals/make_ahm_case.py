#!/usr/bin/env python3
"""Make a synthetic AHM case for the ahm-workflow evals. All names and values are synthetic.

Usage: make_ahm_case.py <outdir>

Result in <outdir>/ahm:
    model/ahm.ert          5 realizations, a '--weights 4,2,2' comment (sum of 1/w is 1.25), no RANDOM_SEED,
                           no UPDATE_LOG_PATH
    dist/params.dist       10 GEN_KW parameters; PERM_D is a permeability with UNIFORM
    dist/truth.txt         PORO_C = 0.14 is outside UNIFORM 0.15-0.30; PERM_A = 750 is near the LOGUNIF upper bound
    obs/hm.obs             36 FOPR, 36 WWCT:P1 (the first 10 are 0 with ABS error 0.04), 12 WBHP:I1 (14 %)
    obs/rft.obs            GENERAL_OBSERVATION RFT_P1 (3 points, good) and RFT_P2 (one error is 0)
    update_log/<id>/       Report.csv and Report.report: 83 active, 1 outlier (WWCT_P1_18)
    ens/realization-0..4/iter-0 and iter-1/parameters.txt   posterior PERM_A piles at 800, PORO_C at 0.15
GROUND_TRUTH holds the facts that a grader and tests/test_ahm_qc.py compare with.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

GROUND_TRUTH = {
    "summary_observations": 84,
    "bhp_share": 12 / 84,
    "zero_wwct": 10,
    "truth_outside": ["PORO_C"],
    "truth_near_bound": ["PERM_A"],
    "linear_perm": ["PERM_D"],
    "uncertain_parameters": 10,
    "realizations": 5,
    "active": 83,
    "outlier": ["WWCT_P1_18"],
    "alpha": 3.0,
    "weights_sum_inverse": 1.25,
    "posterior_at_bound": ["PORO_C", "PERM_A"],
    "general_ok": "RFT_P1",
    "general_bad": "RFT_P2",
}

DIST = """\
-- Synthetic prior. Syntax: NAME DISTRIBUTION arguments
PORO_A   UNIFORM  0.15 0.30
PORO_B   UNIFORM  0.15 0.30
PORO_C   UNIFORM  0.15 0.30
PERM_A   LOGUNIF  20   800
PERM_B   LOGUNIF  20   800
PERM_C   LOGUNIF  20   800
PERM_D   UNIFORM  10   1000
KVKH     LOGUNIF  0.01 1.0
NW       UNIFORM  1.5  4.0
KRWEND   UNIFORM  0.3  0.9
"""
TRUTH = "PORO_A 0.20\nPORO_B 0.22\nPORO_C 0.14\nPERM_A 750\nPERM_B 100\nPERM_C 40\n"


def month(index: int) -> date:
    return date(2020 + index // 12, index % 12 + 1, 1)


def observations() -> list[tuple[str, str, str, float, float]]:
    """(name, key, date, value, error) of each summary observation."""
    rows = []
    for i in range(36):
        rows.append((f"FOPR_{i + 1:02d}", "FOPR", month(i).isoformat(), 400.0 + 10 * (i % 5), 25.0))
    for i in range(36):
        value = 0.0 if i < 10 else round(min(0.6, 0.025 * (i - 9)), 4)
        rows.append((f"WWCT_P1_{i + 1:02d}", "WWCT:P1", month(i).isoformat(), value, 0.04))
    for i in range(12):
        rows.append((f"WBHP_I1_{i + 1:02d}", "WBHP:I1", month(3 * i).isoformat(), 250.0 + i, 5.0))
    return rows


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make(outdir: Path) -> Path:
    root = outdir / "ahm"
    write(
        root / "model/ahm.ert",
        "-- Synthetic AHM config for evals. Planned run: ert es_mda ahm.ert --weights 4,2,2\n"
        "NUM_REALIZATIONS 5\n"
        "QUEUE_SYSTEM LOCAL\n"
        f"RUNPATH {root}/ens/realization-<IENS>/iter-<ITER>\n"
        f"ENSPATH {root}/ens/storage\n"
        "ECLBASE CASE-<IENS>\n"
        "OBS_CONFIG ../obs/hm.obs\n"
        "GEN_KW GLOBVAR ../dist/params.dist\n"
        "ENKF_ALPHA 3.0\n"
        "FORWARD_MODEL FLOW(<ECLBASE>=<ECLBASE>)\n",
    )
    write(root / "dist/params.dist", DIST)
    write(root / "dist/truth.txt", TRUTH)
    rows = observations()
    write(
        root / "obs/hm.obs",
        "-- Synthetic observations\n"
        + "".join(
            f"SUMMARY_OBSERVATION {n} {{ VALUE = {v}; ERROR = {e}; DATE = {d}; KEY = {k}; }};\n"
            for n, k, d, v, e in rows
        ),
    )
    write(root / "obs/rft_p1.txt", "250.0 2.0\n252.0 2.0\n255.0 2.0\n")
    write(root / "obs/rft_p2.txt", "260.0 2.0\n262.0 0.0\n")
    write(
        root / "obs/rft.obs",
        "GENERAL_OBSERVATION RFT_P1 { DATA = RFT_P1; RESTART = 1; OBS_FILE = rft_p1.txt; INDEX_LIST = 0,1,2; };\n"
        "GENERAL_OBSERVATION RFT_P2 { DATA = RFT_P2; RESTART = 1; OBS_FILE = rft_p2.txt; INDEX_LIST = 0,1; };\n",
    )

    header = (
        "observation_key,index,observations,std,obs_error_scaling,scaled_obs_error,"
        "response_mean,response_std,status,missing_realizations\n"
    )
    csv_text = io.StringIO()
    csv_text.write(header)
    writer = csv.writer(csv_text)
    table = []
    for n, _, d, v, e in rows:
        status = "Deactivated, outlier" if n == "WWCT_P1_18" else "Active"
        early_zero = n.startswith("WWCT") and v == 0.0
        std = 0.0001 if early_zero else round(2.0 * e, 4)
        mean = v + (0.5 if status != "Active" else 0.0)
        writer.writerow([n, f"{d} 00:00:00.000", v, e, 1.0, e, mean, std, status, ""])
        table.append(
            f"│ {n:<12} │ {d} 00:00:00.000 │ {v:>9} │ {e:>6} │ 1.0 │ {e:>6} │ {mean:>9} │ {std:>7} │ {status:<20} │  │"
        )
    step = root / "update_log/00000000-synthetic"
    write(step / "Report.csv", csv_text.getvalue())
    write(
        step / "Report.report",
        "Parent ensemble: iter-0\nTarget ensemble: iter-1\nAlpha: 3.0\nGlobal scaling: 1.0\n"
        "Standard cutoff: 1e-06\nActive observations: 83\n"
        "Deactivated observations: missing respons(es): 0\n"
        "Deactivated observations: ensemble_std < STD_CUTOFF: 0\n"
        "Deactivated observations: outliers: 1\n"
        "┌──┐\n│ observation_key │ index │ observations │ std │ obs_error_scaling │ scaled_obs_error │"
        " response_mean │ response_std │ status │ missing_realizations │\n├──┤\n"
        + "\n".join(table)
        + "\n└──┘\n",
    )

    names = ["PORO_A", "PORO_B", "PORO_C", "PERM_A", "PERM_B", "PERM_C", "PERM_D", "KVKH", "NW", "KRWEND"]
    prior = {
        "PORO_A": [0.16, 0.19, 0.22, 0.25, 0.28],
        "PORO_B": [0.17, 0.20, 0.23, 0.26, 0.29],
        "PORO_C": [0.16, 0.18, 0.21, 0.24, 0.27],
        "PERM_A": [30.0, 80.0, 150.0, 300.0, 600.0],
        "PERM_B": [25.0, 60.0, 120.0, 250.0, 500.0],
        "PERM_C": [22.0, 50.0, 110.0, 220.0, 450.0],
        "PERM_D": [50.0, 250.0, 500.0, 750.0, 950.0],
        "KVKH": [0.02, 0.05, 0.1, 0.3, 0.8],
        "NW": [1.7, 2.2, 2.7, 3.2, 3.8],
        "KRWEND": [0.35, 0.45, 0.6, 0.7, 0.85],
    }
    posterior = {
        **prior,
        "PERM_A": [790.0, 780.0, 795.0, 770.0, 600.0],
        "PORO_C": [0.15, 0.151, 0.152, 0.16, 0.17],
    }
    for iteration, values in (("iter-0", prior), ("iter-1", posterior)):
        for real in range(5):
            text = "".join(f"GLOBVAR:{name} {values[name][real]}\n" for name in names)
            write(root / f"ens/realization-{real}/{iteration}/parameters.txt", text)
    return root


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    print(make(Path(args[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
