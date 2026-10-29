#!/usr/bin/env python3
"""Check that a case can give flow diagnostics, and list its injectors and producers, with resdata.

ResInsight flow diagnostics needs the inter-cell fluxes in the restart file. Flow writes them with
  RPTRST
   BASIC=2 FLORES /
(FLOWS also works). Without them ResInsight gives empty results. Writes preflight.json. Exit 0 the
step has fluxes, 2 it has none (the message gives the keyword) or an input is missing.
Measured on Egg: the fluxes make the UNRST 29% larger (36.7 to 47.4 MB). Rerun one realization only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import warnings
from pathlib import Path

FLUX_PREFIXES = ("FLROIL", "FLRWAT", "FLRGAS", "FLOOIL", "FLOWAT", "FLOGAS")
GROWTH = 1.29  # measured on Egg with FLORES; used for the disk estimate


class InputError(Exception):
    """Exit 2."""


def resolve_step(dates: list[str], step: str) -> int:
    if step == "last":
        return len(dates) - 1
    if step.isdigit() and int(step) < len(dates):
        return int(step)
    if step in dates:
        return dates.index(step)
    raise InputError(f"--step {step} is not a report step. Steps: {dates[0]} .. {dates[-1]} ({len(dates)})")


def flux_steps(rst) -> tuple[list[str], list[int]]:
    """The flux keywords and the report steps that have non-zero fluxes. Read each step by its own
    view: restart_get_kw(name, date) counts occurrences of the keyword, so a step without the
    keyword shifts every later step. Measured on Egg (Flow 2026.04, BASIC=2 FLORES): step 0 has no
    flux arrays, step 1 has zero fluxes, steps 2 to the last have fluxes."""
    import numpy as np

    names = sorted(name for name in rst.keys() if name.startswith(FLUX_PREFIXES))
    steps = []
    if names:
        probe = names[0]
        for index in range(len(rst.dates)):
            view = rst.restart_view(seqnum_index=index)
            if probe in view and float(np.abs(view[probe][0].numpy_copy()).sum()) > 0:
                steps.append(index)
    return names, steps


def classify(base: Path, date: str) -> tuple[list[str], list[str]]:
    """Injectors: WWIR or WGIR > 0 at the step. Producers: WOPR, WWPR or WGPR > 0 at the step."""
    import datetime as dt

    from resdata.summary import Summary

    smspec = Path(f"{base}.SMSPEC")
    if not smspec.is_file():
        raise InputError(
            f"missing {smspec.name}: cannot find the injectors and producers. Give --injectors and --producers"
        )
    summary = Summary(str(smspec))
    when = max(
        dt.datetime.strptime(date, "%Y-%m-%d"), summary.dates[0]
    )  # step 0 is before the first summary date
    injectors, producers = [], []

    def rate(vector: str, well: str) -> float:
        key = f"{vector}:{well}"
        return float(summary.get_interp(key, date=when)) if summary.has_key(key) else 0.0  # noqa: W601

    for well in summary.wells():
        if rate("WWIR", well) > 0 or rate("WGIR", well) > 0:
            injectors.append(well)
        elif rate("WOPR", well) > 0 or rate("WWPR", well) > 0 or rate("WGPR", well) > 0:
            producers.append(well)
    return injectors, producers


def run(args: argparse.Namespace) -> int:
    from resdata.resfile import ResdataFile

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    base = (
        args.case.with_suffix("") if args.case.suffix.upper() in (".EGRID", ".DATA", ".UNRST") else args.case
    )
    unrst = Path(f"{base}.UNRST")
    for path in (Path(f"{base}.EGRID"), unrst):
        if not path.is_file():
            raise InputError(f"missing {path}")
    if base.resolve().parent in args.out.resolve().parents:
        raise InputError(f"--out {args.out} is in the case folder. Choose a folder outside it")
    rst = ResdataFile(str(unrst))
    dates = [d.strftime("%Y-%m-%d") for d in rst.dates]
    index = resolve_step(dates, args.step)
    names, steps = flux_steps(rst)
    size = unrst.stat().st_size
    result = {
        "case": str(base),
        "step": index,
        "date": dates[index],
        "flux_keywords": names,
        "flux_steps": steps,
        "unrst_bytes": size,
    }
    if names and index not in steps:
        raise InputError(
            f"step {index} ({dates[index]}) has no non-zero fluxes. Steps with fluxes: "
            f"{steps[0]}..{steps[-1]}" + (f". Use --step {steps[-1]} ({dates[steps[-1]]})" if steps else "")
        )
    if not names:
        free = shutil.disk_usage(unrst.parent).free
        need = int(size * GROWTH)
        result["advice"] = (
            "No flux arrays at this step. Add 'RPTRST' / ' BASIC=2 FLORES /' to the SCHEDULE (or the "
            "SOLUTION section), rerun one realization into a new folder, and run this check again. "
            f"Expected UNRST about {need / 1e6:.0f} MB; {free / 1e9:.0f} GB free in {unrst.parent}"
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"ERROR: {result['advice']}", file=sys.stderr)
        return 2
    if args.injectors or args.producers:
        injectors = [w for w in (args.injectors or "").split(",") if w]
        producers = [w for w in (args.producers or "").split(",") if w]
        result["wells_from"] = "arguments"
    else:
        injectors, producers = classify(base, dates[index])
        result["wells_from"] = "summary rates at the step"
    if not injectors or not producers:
        raise InputError(f"injectors {injectors}, producers {producers}: flow diagnostics needs one of each")
    result |= {"injectors": injectors, "producers": producers}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case", required=True, type=Path, help="BASE or BASE.EGRID (with UNRST and SMSPEC)")
    parser.add_argument("--step", default="last", help="Report step: an index, a date YYYY-MM-DD, or last")
    parser.add_argument("--injectors", help="Comma-separated. Default: from the summary rates at the step")
    parser.add_argument("--producers", help="Comma-separated. Default: from the summary rates at the step")
    parser.add_argument("--out", required=True, type=Path, help="preflight.json to write")
    args = parser.parse_args(argv)
    try:
        import resdata  # noqa: F401
    except ImportError:
        print("ERROR: resdata is not installed. Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        return run(args)
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
