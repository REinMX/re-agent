#!/usr/bin/env python3
"""Make a synthetic ERT case with known failures. Use it as ground truth for the ert-diagnose evals.

Usage: make_failed_ensemble.py <outdir>

Result: <outdir>/case/realization-0..11/iter-0, with the files that ERT 23 writes.
Ground truth is in GROUND_TRUTH. A grader compares the agent answer with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# realization -> (state, failed step, failure class)
GROUND_TRUTH: dict[int, tuple[str, str | None, str | None]] = {
    0: ("ok", None, None),
    1: ("ok", None, None),
    2: ("ok", None, None),
    3: ("ok", None, None),
    4: ("ok", None, None),
    5: ("failed", "FLOW", "killed"),
    6: ("failed", "FLOW", "killed"),
    7: ("failed", "COPY_FILE", "missing_file"),
    8: ("failed", "FLOW", "simulator_input"),
    9: ("incomplete", None, None),
    10: ("ok", None, None),  # OPM Flow chopped time steps and finished. Not a failure.
    11: ("failed", "FLOW", "convergence"),
}

# Real lines from an OPM Flow PRT file (SPE1 with --newton-max-iterations=3). The run finished with exit code 0.
OPM_CHOPS = (
    "Problem: Solver convergence failure - Iteration limit reached\n"
    "Timestep chopped to 6.398844072337535 days\n\n"
    "Problem: Solver convergence failure - Iteration limit reached\n"
    "Timestep chopped to 4.2872255284661485 days\n\n"
    "Problems          2\n"
)

STDERR = {
    "killed": "run_flow.sh: line 12: 31337 Killed   flow CASE.DATA\n",
    "missing_file": "cp: cannot stat '../../rms/output/grid.roff': No such file or directory\n",
    "simulator_input": "",
    "convergence": "",
}


def write_realization(case: Path, iens: int) -> None:
    state, step_name, failure = GROUND_TRUTH[iens]
    path = case / f"realization-{iens}" / "iter-0"
    (path / "eclipse/model").mkdir(parents=True)
    (path / "parameters.txt").write_text(f"MULTFLT 0.{iens}\nPORO_SCALE 1.0\n")
    steps = [
        {
            "name": "COPY_FILE",
            "status": "Success",
            "error": None,
            "stdout": "COPY_FILE.stdout.0",
            "stderr": "COPY_FILE.stderr.0",
        },
        {
            "name": "FLOW",
            "status": "Success",
            "error": None,
            "stdout": "FLOW.stdout.1",
            "stderr": "FLOW.stderr.1",
        },
    ]
    if state == "ok":
        (path / "OK").write_text("All jobs complete\n")
        if iens == 10:
            (path / "eclipse/model/CASE.PRT").write_text("Starting\n" + OPM_CHOPS)
    elif state == "incomplete":
        steps[1]["status"] = "Running"
    else:
        index = 0 if step_name == "COPY_FILE" else 1
        steps[index]["status"] = "Failure"
        steps[index]["error"] = f"The run of {step_name} failed"
        (path / "ERROR").write_text(f"Step {step_name} failed\n")
        stderr_name = steps[index]["stderr"]
        (path / stderr_name).write_text(STDERR[failure])
        if failure == "convergence":
            (path / "eclipse/model/CASE.PRT").write_text("Starting\n" + OPM_CHOPS)
        if failure == "simulator_input":
            (path / "eclipse/model/CASE.PRT").write_text(
                "Starting\n@--ERROR  Unknown keyword WCONPROD2 at line 412\n"
            )
    (path / "status.json").write_text(json.dumps({"steps": steps}, indent=2))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    case = Path(sys.argv[1]) / "case"
    for iens in GROUND_TRUTH:
        write_realization(case, iens)
    print(f"Wrote {len(GROUND_TRUTH)} realizations in {case}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
