#!/usr/bin/env python
"""Copy the public Drogon AHM case and install this overlay into the new destination."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1]


def patch_deck(text):
    for old, new in [("grid/drogon.perm", "ert/perm.inc"), ("props/drogon.swatinit", "ert/swatinit.inc")]:
        if text.count(old) != 1:
            raise ValueError(f"expected exactly one {old} include")
        text = text.replace(old, new)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True, help="new destination, must not exist")
    parser.add_argument("--source", type=Path, required=True, help="baseline public AHM case, read only")
    parser.add_argument("--truth", type=Path, required=True, help="truth ECLBASE without suffix")
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    case = args.case.resolve()
    source = args.source.resolve()
    if case.exists() or case == source or source.is_relative_to(case) or case.is_relative_to(source):
        parser.error("destination must be a new path separate from the source")
    if not (source / "deck/model/DROGON_AHM.DATA").is_file():
        parser.error("source must contain the baseline public Drogon AHM deck")
    shutil.copytree(
        source,
        case,
        ignore=shutil.ignore_patterns("output", "storage", "scratch*", "worklog", "logs", "__pycache__"),
    )
    shutil.copy2(EXAMPLE / "tools/run_es.sh", case / "run_es.sh")
    (case / "run_es.sh").chmod(0o755)
    for folder in ("jobs", "scal"):
        shutil.copytree(EXAMPLE / folder, case / folder, dirs_exist_ok=True)
    for p in (case / "jobs").glob("*.py"):
        p.chmod(0o755)
    for p in (EXAMPLE / "ert").rglob("*"):
        if p.is_file():
            target = case / "ert" / p.relative_to(EXAMPLE / "ert")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                p.read_text().replace("<CONFIG_PATH>/../scratch_v2", str(args.scratch.resolve()))
            )
    target = case / "deck/model/DROGON_V2.DATA"
    target.write_text(patch_deck((case / "deck/model/DROGON_AHM.DATA").read_text()))
    static = case / "static"
    static.mkdir(exist_ok=True)
    shutil.copy2(str(args.truth) + ".EGRID", static / "DROGON.EGRID")
    commands = [
        [
            EXAMPLE / "tools/make_static.py",
            "--run",
            args.truth,
            "--deck",
            case / "deck",
            "--out",
            static / "drogon_static.npz",
        ],
        [
            EXAMPLE / "tools/make_fields.py",
            "--static",
            static / "drogon_static.npz",
            "--out",
            static / "fields",
        ],
        [
            EXAMPLE.parents[1] / "skills/hm-localization/scripts/obs_locations.py",
            "--data",
            str(args.truth) + ".DATA",
            "--grid",
            str(args.truth) + ".EGRID",
            "--out",
            static / "locations.csv",
        ],
        [
            EXAMPLE / "tools/make_obs.py",
            "--truth",
            args.truth,
            "--old-obs",
            case / "ert/obs/drogon.obs",
            "--locations",
            static / "locations.csv",
            "--out",
            case / "ert/obs/after.obs",
        ],
    ]
    for command in commands:
        subprocess.run([sys.executable, *map(str, command)], check=True)


if __name__ == "__main__":
    main()
