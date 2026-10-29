#!/usr/bin/env python3
"""Make the material-balance test data. Needs OPM Flow and res2df. Run it again after a deck changes.

For each deck in this folder: run Flow in a temporary folder, then run extract_case.py.
The result is <CASE>/history.csv, <CASE>/pvt.csv, and <CASE>/reference.json in this folder.
Usage: make_mbal_data.py [--flow flow] [--python <python with res2df>]
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRACT = HERE.parents[2] / "skills/material-balance/scripts/extract_case.py"
DECKS = ["OILBOX", "OILBOX_AQ", "GASBOX"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", default="flow")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    for name in DECKS:
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / f"{name}.DATA"
            shutil.copy2(HERE / f"{name}.DATA", deck)
            subprocess.run([args.flow, str(deck), f"--output-dir={tmp}"], check=True, capture_output=True)
            subprocess.run([args.python, str(EXTRACT), str(deck), "--outdir", str(HERE / name)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
