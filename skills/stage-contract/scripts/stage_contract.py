#!/usr/bin/env python3
"""Load and validate site.contract.yml (schema v2.1, or a v1 flat contract) for the agent.

Uses the shared loader `contract_lib.py` (same folder): file lookup, profile selection,
project overlay, variable expansion. Then it checks the resolved contract.

Exit codes:
    0  contract valid
    2  missing file, unknown profile, missing keys, placeholders, or invalid values
    1  unexpected error

Usage:
    stage_contract.py [CONTRACT] [--profile NAME] [--json]
    stage_contract.py                 # lookup: env RE_AGENT_CONTRACT, ./site.contract.yml, user file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import contract_lib  # noqa: E402
from contract_lib import ContractError, find_placeholders  # noqa: E402

REQUIRED_PATHS = ("deliverables",)
OPTIONAL_PATHS = ("root", "project", "scratch")
REQUIRED_SCENARIO_QC = (
    "baseline",
    "tolerances",
    "require_fingerprint_match_runmeta",
    "fail_on_missing_vector",
)
KNOWN_SIMULATORS = frozenset({"FLOW", "ECLIPSE100", "ECLIPSE300", "IX"})
GATE_MODES = ("draft", "strict")


def _flag(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("enabled")
    if value in (False, "disabled", None):
        return "disabled"
    if value in (True, "enabled"):
        return "enabled"
    return str(value)


def validate(c: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return (summary, errors, notes) for a resolved contract (output of load_contract)."""
    errors: list[str] = []
    notes: list[str] = []

    for key in contract_lib.REQUIRED_PROJECT_KEYS:
        try:
            contract_lib.Contract(c).require(key)
        except ContractError as exc:
            errors.append(str(exc))

    p10 = str(c.get("p10_p90") or "").strip().lower()
    if p10 and p10 not in contract_lib.P10_P90:
        errors.append(f"p10_p90 must be one of {sorted(contract_lib.P10_P90)}, got {p10!r}")

    req_sum = contract_lib.get_dotted(c, "vectors.required_summary")
    if req_sum is not None:
        if not isinstance(req_sum, list) or not req_sum:
            errors.append("vectors.required_summary must be a non-empty list")
        elif not all(isinstance(x, str) and x.strip() for x in req_sum):
            errors.append("vectors.required_summary entries must be non-empty strings")

    sim = c.get("simulator")
    sim_name = sim.get("name") if isinstance(sim, dict) else sim
    if not sim_name:
        errors.append("simulator.name is required in the profile (e.g. FLOW, ECLIPSE100)")
    elif str(sim_name) not in KNOWN_SIMULATORS:
        notes.append(
            f"simulator.name {sim_name!r} is not in {sorted(KNOWN_SIMULATORS)}; accepted as configured"
        )

    paths = c.get("paths")
    if not isinstance(paths, dict):
        errors.append("paths must be a mapping")
        paths = {}
    for key in REQUIRED_PATHS + OPTIONAL_PATHS:
        if key in paths and (not isinstance(paths[key], str) or not paths[key].strip()):
            errors.append(f"paths.{key} must be a non-empty string")

    sq = c.get("scenario_qc")
    if sq is not None:
        if not isinstance(sq, dict):
            errors.append("scenario_qc must be a mapping")
        else:
            for key in REQUIRED_SCENARIO_QC:
                if key not in sq:
                    errors.append(f"scenario_qc.{key}")
            if "baseline" in sq and (not isinstance(sq["baseline"], str) or not sq["baseline"].strip()):
                errors.append("scenario_qc.baseline must be a non-empty string")
            if "tolerances" in sq and (not isinstance(sq["tolerances"], dict) or not sq["tolerances"]):
                errors.append("scenario_qc.tolerances must be a non-empty mapping")
            for bkey in ("require_fingerprint_match_runmeta", "fail_on_missing_vector"):
                if bkey in sq and not isinstance(sq[bkey], bool):
                    errors.append(f"scenario_qc.{bkey} must be a boolean")

    gs = c.get("gate_stages")
    if gs is not None:
        if not isinstance(gs, dict) or not gs:
            errors.append("gate_stages must be a non-empty mapping")
        else:
            for sname, scfg in gs.items():
                if not isinstance(scfg, dict):
                    errors.append(f"gate_stages.{sname} must be a mapping")
                    continue
                if scfg.get("mode_default") not in GATE_MODES:
                    errors.append(
                        f"gate_stages.{sname}.mode_default must be 'draft' or 'strict', "
                        f"got {scfg.get('mode_default')!r}"
                    )
                arts = scfg.get("required_artifacts")
                if not isinstance(arts, list) or not arts:
                    errors.append(f"gate_stages.{sname}.required_artifacts must be a non-empty list")
            default = c.get("gate_default_stage")
            if default is not None and default not in gs:
                errors.append(f"gate_default_stage {default!r} is not a key of gate_stages {sorted(gs)}")

    for key in find_placeholders(c):
        if key in ("model_endpoint",):
            continue
        errors.append(f"{key}: still holds a <placeholder>; fill it in the local contract")

    if errors:
        return {}, errors, notes

    summary = {
        "profile": getattr(c, "profile_name", ""),
        "sources": list(getattr(c, "sources", [])),
        "schema_version": getattr(c, "schema_version", None),
        "project": c.get("project"),
        "simulator": sim_name,
        "eclbase": c.get("eclbase"),
        "p10_p90": p10,
        "p10_p90_note": (
            "numerical percentiles (low=P10, high=P90)"
            if p10 == "numerical"
            else "exceedance percentiles (P10=upside)"
        ),
        "required_summary": list(req_sum or []),
        "paths": {k: paths[k] for k in REQUIRED_PATHS + OPTIONAL_PATHS if k in paths},
        "everest": _flag(c.get("everest")),
        "sumo": _flag(c.get("sumo")),
        "queue": c.get("queue"),
        "activate_shell": c.get("activate_shell"),
    }
    if isinstance(sq, dict):
        summary["scenario_qc"] = {
            "baseline": sq["baseline"],
            "tolerances": dict(sq["tolerances"]),
            "require_fingerprint_match_runmeta": sq["require_fingerprint_match_runmeta"],
            "fail_on_missing_vector": sq["fail_on_missing_vector"],
        }
    if isinstance(gs, dict):
        summary["gate_stages"] = {
            name: {
                "mode_default": cfg.get("mode_default"),
                "required_artifacts": list(cfg.get("required_artifacts") or []),
            }
            for name, cfg in gs.items()
            if isinstance(cfg, dict)
        }
        summary["gate_default_stage"] = c.get("gate_default_stage")
    return summary, [], notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate site.contract.yml (stage-contract).")
    parser.add_argument("contract", type=Path, nargs="?", default=None, help="Path to site.contract.yml")
    parser.add_argument("--contract", dest="contract_opt", type=Path, default=None, help="Same as CONTRACT")
    parser.add_argument(
        "--profile", default=None, help="Profile (default: env RE_AGENT_PROFILE, active_profile)"
    )
    parser.add_argument("--json", action="store_true", help="Print validated summary as JSON")
    args = parser.parse_args(argv)
    path = args.contract or args.contract_opt

    try:
        resolved = contract_lib.load_contract(path, args.profile, require_project=False)
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2

    summary, errors, notes = validate(resolved)
    for note in list(resolved.warnings) + notes:
        sys.stderr.write(f"note: {note}\n")
    if errors:
        sys.stderr.write(f"error: invalid contract ({', '.join(resolved.sources)})\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        return 2

    if args.json:
        json.dump(summary, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
        return 0
    sys.stdout.write(f"contract: ok ({', '.join(summary['sources'])})\n")
    sys.stdout.write(f"profile: {summary['profile']} (schema {summary['schema_version']})\n")
    sys.stdout.write(f"project: {summary['project']}\n")
    sys.stdout.write(f"simulator: {summary['simulator']}\n")
    sys.stdout.write(f"eclbase: {summary['eclbase']}\n")
    sys.stdout.write(f"p10_p90: {summary['p10_p90']} ({summary['p10_p90_note']})\n")
    sys.stdout.write(f"required_summary: {', '.join(summary['required_summary'])}\n")
    for key, val in summary["paths"].items():
        sys.stdout.write(f"paths.{key}: {val}\n")
    sys.stdout.write(f"everest: {summary['everest']}\n")
    sys.stdout.write(f"sumo: {summary['sumo']}\n")
    if "scenario_qc" in summary:
        sq = summary["scenario_qc"]
        sys.stdout.write(f"scenario_qc.baseline: {sq['baseline']}\n")
        sys.stdout.write(f"scenario_qc.fail_on_missing_vector: {sq['fail_on_missing_vector']}\n")
        sys.stdout.write(
            f"scenario_qc.require_fingerprint_match_runmeta: {sq['require_fingerprint_match_runmeta']}\n"
        )
    if "gate_stages" in summary:
        sys.stdout.write(f"gate_stages: {', '.join(summary['gate_stages'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
