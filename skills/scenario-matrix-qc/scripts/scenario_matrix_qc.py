#!/usr/bin/env python3
"""Roll up scenario-qc reports for all scenarios under scenarios/.

Thin matrix: one row per scenario id with status, flags, headline rel_delta
(contract scenario_qc.headline_vector, default FOPT).
Does not re-run sims. Optionally invokes scenario_qc per scenario.

Usage:
  scenario_matrix_qc.py --contract site.contract.yml
  scenario_matrix_qc.py --contract ... --run-qc   # run scenario_qc for each non-baseline
  scenario_matrix_qc.py --contract ... --outdir deliverables/scenario_qc

Exit: 0 all pass (or only baseline), 2 any FAIL, 1 unexpected.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402


def scenarios_root(contract: dict[str, Any]) -> Path:
    paths = contract.get("paths") or {}
    if paths.get("scenarios"):
        return Path(paths["scenarios"]).resolve()
    if paths.get("root"):
        return Path(paths["root"]).resolve() / "scenarios"
    if not paths.get("project"):
        raise ContractError("contract needs paths.scenarios, paths.root or paths.project")
    p_proj = Path(paths["project"]).resolve()
    base = p_proj.parent if p_proj.name == "project" else p_proj
    return (base / "scenarios").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scenario matrix QC rollup.")
    add_contract_args(parser)
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument(
        "--run-qc",
        action="store_true",
        help="Invoke scenario_qc.py for each non-baseline scenario before rollup",
    )
    parser.add_argument(
        "--include-baseline",
        action="store_true",
        help="Also QC baseline vs itself (expect PASS)",
    )
    parser.add_argument(
        "--exclude-demo",
        action="store_true",
        help="Skip demo_* scenarios and runmeta.demo truthy (gate matrix)",
    )
    parser.add_argument(
        "--include-demo",
        action="store_true",
        help="Force include demos even if contract scenario_qc.exclude_demo is true",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        if not isinstance(contract.get("scenario_qc"), dict):
            raise ContractError("contract key `scenario_qc` is required for the scenario matrix")
        root = scenarios_root(contract)
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    headline = str((contract.get("scenario_qc") or {}).get("headline_vector") or "FOPT").upper()
    if not root.is_dir():
        sys.stderr.write(f"error: scenarios root not found: {root}\n")
        return 2

    baseline_rel = contract["scenario_qc"]["baseline"]
    baseline_name = Path(baseline_rel).name

    qc_script = Path(__file__).resolve().parents[2] / "scenario-qc" / "scripts" / "scenario_qc.py"
    py = contract.get("python")
    if not py or not Path(str(py)).is_file():
        py = sys.executable
    contract_args = (["--contract", str(args.contract)] if args.contract else []) + [
        "--profile",
        contract.profile_name,
    ]
    if contract.profile_name == contract_lib.LEGACY_PROFILE:
        contract_args = contract_args[:-2]

    rows: list[dict[str, Any]] = []
    any_fail = False

    sq = contract.get("scenario_qc") or {}
    exclude_demo = bool(args.exclude_demo) or bool(sq.get("exclude_demo", False))
    if args.include_demo:
        exclude_demo = False

    def _is_demo(sdir: Path) -> bool:
        if sdir.name.startswith("demo_"):
            return True
        meta_path = sdir / "runmeta.yml"
        if meta_path.is_file():
            try:
                import yaml

                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception:
                return False
            demo_val = meta.get("demo")
            if demo_val is True or (isinstance(demo_val, str) and demo_val.strip()):
                # string demo labels (fingerprint_runmeta_mismatch) count as demo
                if demo_val is True:
                    return True
                if isinstance(demo_val, str) and demo_val.lower() not in ("false", "0", "no"):
                    return True
        return False

    scenario_dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    excluded_demos: list[str] = []
    for sdir in scenario_dirs:
        if exclude_demo and _is_demo(sdir):
            excluded_demos.append(sdir.name)
            rows.append(
                {
                    "scenario_id": sdir.name,
                    "status": "EXCLUDED_DEMO",
                    "flags": "excluded_from_gate_matrix",
                    "headline_rel_delta": "",
                    "report": "",
                }
            )
            continue
        if sdir.name == baseline_name and not args.include_baseline:
            # still list baseline as reference row without QC
            rows.append(
                {
                    "scenario_id": sdir.name,
                    "status": "BASELINE",
                    "flags": "",
                    "headline_rel_delta": "",
                    "report": "",
                }
            )
            continue

        if args.run_qc or args.include_baseline:
            if not qc_script.is_file():
                sys.stderr.write(f"error: scenario_qc.py not found at {qc_script}\n")
                return 1
            proc = subprocess.run(
                [str(py), str(qc_script), *contract_args, "--scenario", str(sdir)],
                capture_output=True,
                text=True,
            )
            if proc.returncode not in (0, 2):
                sys.stderr.write(f"error: scenario_qc crashed for {sdir.name}: {proc.stderr}\n")
                return 1

        report_path = sdir / "deliverables" / "scenario_qc" / "scenario_qc_report.json"
        if not report_path.is_file():
            rows.append(
                {
                    "scenario_id": sdir.name,
                    "status": "NO_REPORT",
                    "flags": "missing_report",
                    "headline_rel_delta": "",
                    "report": str(report_path),
                }
            )
            any_fail = True
            continue

        report = json.loads(report_path.read_text(encoding="utf-8"))
        fopt = (report.get("checks") or {}).get("field") or {}
        fopt_rel = ""
        if headline in fopt:
            fopt_rel = f"{fopt[headline]['rel_delta']:.6g}"
        status = report.get("status", "UNKNOWN")
        flags = ",".join(report.get("flags") or [])
        if status == "FAIL":
            any_fail = True
        rows.append(
            {
                "scenario_id": report.get("scenario_id") or sdir.name,
                "status": status,
                "flags": flags,
                "headline_rel_delta": fopt_rel,
                "report": str(report_path),
            }
        )

    outdir = args.outdir
    if outdir is None:
        deliverables = contract.path("deliverables")
        outdir = deliverables / "scenario_qc"
    outdir.mkdir(parents=True, exist_ok=True)

    csv_path = outdir / "scenario_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["scenario_id", "status", "flags", "headline_rel_delta", "report"]
        )
        writer.writeheader()
        writer.writerows(rows)

    matrix = {
        "skill": "scenario-matrix-qc",
        "p10_p90": contract.get("p10_p90"),
        "headline_vector": headline,
        "scenarios_root": str(root),
        "n_scenarios": len(rows),
        "any_fail": any_fail,
        "exclude_demo": exclude_demo,
        "excluded_demos": excluded_demos,
        "rows": rows,
    }
    (outdir / "scenario_matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    md_lines = [
        "# Scenario matrix QC",
        "",
        f"- P10/P90: {contract.get('p10_p90')}",
        f"- Scenarios root: `{root}`",
        f"- Any fail: {any_fail}",
        "",
        f"| scenario_id | status | flags | {headline} rel_delta |",
        "|---|---|---|---|",
    ]
    for r in rows:
        md_lines.append(
            f"| {r['scenario_id']} | {r['status']} | {r['flags'] or '—'} | {r['headline_rel_delta'] or '—'} |"
        )
    (outdir / "scenario_matrix.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    sys.stdout.write(f"scenario_matrix_qc: {'FAIL' if any_fail else 'PASS'} n={len(rows)} wrote={outdir}\n")
    return 2 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
