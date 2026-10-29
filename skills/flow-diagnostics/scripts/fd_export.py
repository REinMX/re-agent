#!/usr/bin/env python3
"""Run ResInsight flow diagnostics on one case and export the cell results as GRDECL. Needs rips.

Reads preflight.json of fd_preflight.py (the case, the step with fluxes, the injectors and producers).
Writes in --outdir/fd: FRAC<n>.grdecl for injector n (the fraction of the cell flow that comes from it),
FRACP<n>.grdecl for producer n (the fraction that goes to it), RESTIME.grdecl (residence time in days,
all wells), and fd_export.json, which maps each keyword to its well.
These are ResInsight results: the only flow diagnostics solver here. fd_metrics.py turns them into
allocation and F-Phi with PORV from resdata. Exit 0 all exported, 1 some failed, 2 input or ResInsight missing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "resinsight-session" / "scripts"))
sys.path.insert(0, str(HERE.parents[2] / "resinsight-views" / "scripts"))

import grdecl  # noqa: E402
import ri_session as ri  # noqa: E402


def export_one(
    session, view, folder: Path, variable: str, mode: str, injectors, producers, keyword: str, cells: int
) -> Path:
    view.apply_flow_diagnostics_cell_result(
        result_variable=variable, selection_mode=mode, injectors=list(injectors), producers=list(producers)
    )
    raw = session.export_view_property(view, folder / "raw")
    _, values = grdecl.read_grdecl(raw, expected=cells)
    out = folder / f"{keyword}.grdecl"
    grdecl.write_grdecl(out, keyword, values)
    raw.unlink()
    return out


def run(args: argparse.Namespace, rips=None, launcher=None) -> int:
    """rips and launcher: the Session injection points (tests). Default: import rips, launch ResInsight."""
    pre = json.loads(args.preflight.read_text())
    for key in ("case", "step", "injectors", "producers"):
        if key not in pre:
            raise ri.RiError(f"{args.preflight} has no {key}. Run fd_preflight.py first, and fix its errors")
    case_folder = Path(pre["case"]).resolve().parent
    if case_folder in [args.outdir.resolve(), *args.outdir.resolve().parents]:
        raise ri.RiError(f"--outdir {args.outdir} is in the case folder. Choose a folder outside it")
    folder = args.outdir / "fd"
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    manifest = {
        "case": pre["case"],
        "step": pre["step"],
        "date": pre.get("date"),
        "arrays": {},
        "failed": [],
        "source": "ResInsight flow diagnostics (computed by ResInsight, not resdata)",
    }
    config = ri.SessionConfig(
        executable=args.bin, mode="console", workdir=args.outdir / "ri", expected_version=args.expect_version
    )
    with ri.Session(config, rips=rips, launcher=launcher or ri.launch_process) as session:
        manifest["resinsight_version"] = session.version
        case = session.load_case(f"{pre['case']}.EGRID")
        dims = case.grid(0).dimensions()
        cells = dims.i * dims.j * dims.k
        view = session.ensure_view(case)
        view.set_time_step(int(pre["step"]))
        jobs = [(f"FRAC{n}", "Fraction", [w], [], w, "injector") for n, w in enumerate(pre["injectors"], 1)]
        jobs += [(f"FRACP{n}", "Fraction", [], [w], w, "producer") for n, w in enumerate(pre["producers"], 1)]
        jobs.append(("RESTIME", "TOF", [], [], "all", "all"))  # "TOF" with all wells is shown as Res.Time
        for keyword, variable, injectors, producers, well, role in jobs:
            mode = "FLOW_TR_INJ_AND_PROD" if role == "all" else "FLOW_TR_BY_SELECTION"
            try:
                path = export_one(session, view, folder, variable, mode, injectors, producers, keyword, cells)
            except (ri.RiError, grdecl.GrdeclError, Exception) as exc:  # noqa: BLE001 - recorded, run goes on
                manifest["failed"].append(
                    {"keyword": keyword, "well": well, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            manifest["arrays"][keyword] = {"file": str(path), "result": variable, "well": well, "role": role}
    manifest["seconds"] = round(time.perf_counter() - started, 2)
    views = [
        {
            "name": f"drain_{w.lower()}",
            "property": "FLOW_DIAGNOSTICS:Fraction",
            "time_step": int(pre["step"]),
            "flow": {"producers": [w]},
        }
        for w in pre["producers"]
    ]
    views += [
        {
            "name": f"sweep_{w.lower()}",
            "property": "FLOW_DIAGNOSTICS:Fraction",
            "time_step": int(pre["step"]),
            "flow": {"injectors": [w]},
        }
        for w in pre["injectors"][: args.max_injector_views]
    ]
    if "RESTIME" in manifest["arrays"]:
        views.append({"name": "restime", "property": "INPUT_PROPERTY:RESTIME"})
    spec = {
        "case": f"{pre['case']}.EGRID",
        "out": str(args.outdir / "views"),
        "title": f"Flow diagnostics {pre.get('date')}",
        "arrays": [{"name": "RESTIME", "file": manifest["arrays"]["RESTIME"]["file"]}]
        if "RESTIME" in manifest["arrays"]
        else [],
        "views": views,
        "notes": ["Fraction: the share of the cell flow from the injector or to the producer"],
    }
    (args.outdir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    manifest["spec"] = str(args.outdir / "spec.json")
    (args.outdir / "fd_export.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("failed", "seconds")} | {"arrays": len(manifest["arrays"])}))
    return 1 if manifest["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--bin", required=True, type=Path, help="ResInsight executable or the ri launcher")
    parser.add_argument("--expect-version", help="Major.minor that the contract gives, e.g. 2026.09")
    parser.add_argument("--max-injector-views", type=int, default=4, help="Sweep views in spec.json")
    parser.add_argument("--outdir", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.preflight.is_file():
        print(f"ERROR: missing {args.preflight}. Run fd_preflight.py first", file=sys.stderr)
        return 2
    try:
        return run(args)
    except ri.RiError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
