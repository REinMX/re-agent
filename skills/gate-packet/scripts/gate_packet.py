#!/usr/bin/env python3
"""Assemble and gate-check a stage packet. Stages are the keys of contract gate_stages.

Consumes existing deliverables. Invokes qa-qc-preread to write the pre-read.
Does not start Flow/ERT. Does not invent numbers.

Modes:
  --strict            refuse (exit 2) when open issues exist or required artifacts missing
  --allow-open-issues draft mode: write packet even with open issues (listed in pre-read)
  default             from contract gate_stages.<stage>.mode_default
                      (required; no name-based fallback)

Exit: 0 pass (or draft with issues listed), 2 refuse / strict fail, 1 unexpected.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402


def resolve_mode(stage: str, stage_cfg: dict[str, Any], args: argparse.Namespace) -> str:
    if args.strict and args.allow_open_issues:
        raise ValueError("use only one of --strict or --allow-open-issues")
    if args.strict:
        return "strict"
    if args.allow_open_issues:
        return "draft"
    mode = str(stage_cfg.get("mode_default") or "").strip().lower()
    if mode not in {"strict", "draft"}:
        # No name-based fallback: the contract must say draft or strict.
        raise ValueError(f"gate_stages.{stage}.mode_default must be 'draft' or 'strict', got {mode!r}")
    return mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate packet checklist + pre-read.")
    add_contract_args(parser)
    parser.add_argument(
        "--stage", default=None, help="A key of contract gate_stages (default: contract gate_default_stage)"
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Default: deliverables/gate_packets/<stage>/",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Refuse when open issues or missing required artifacts",
    )
    parser.add_argument(
        "--allow-open-issues",
        action="store_true",
        help="Draft mode: write packet with open issues listed",
    )
    parser.add_argument(
        "--skip-preread",
        action="store_true",
        help="Do not invoke qa-qc-preread (use existing preread_outline.json)",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        stage, stage_cfg = contract_lib.select_gate_stage(contract, args.stage)
        deliv = contract.path("deliverables").resolve()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    if not stage_cfg.get("required_artifacts"):
        sys.stderr.write(f"error: contract: gate_stages.{stage}.required_artifacts is empty\n")
        return 2
    if not deliv.is_dir():
        sys.stderr.write(f"error: deliverables missing: {deliv}\n")
        return 2
    contract_label = Path(contract.sources[-1]) if contract.sources else Path("site.contract.yml")

    try:
        mode = resolve_mode(stage, stage_cfg, args)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    outdir = args.outdir
    if outdir is None:
        outdir = deliv / "gate_packets" / stage
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    preread_script = Path(__file__).resolve().parents[2] / "qa-qc-preread" / "scripts" / "qa_qc_preread.py"
    py = contract.get("python")
    if not py or not Path(str(py)).is_file():
        py = sys.executable
    contract_args = ["--contract", str(args.contract)] if args.contract else []
    if contract.profile_name != contract_lib.LEGACY_PROFILE:
        contract_args += ["--profile", contract.profile_name]

    if not args.skip_preread:
        if not preread_script.is_file():
            sys.stderr.write(f"error: qa-qc-preread script missing: {preread_script}\n")
            return 2
        proc = subprocess.run(
            [
                str(py),
                str(preread_script),
                *contract_args,
                "--stage",
                stage,
                "--outdir",
                str(outdir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr or proc.stdout or "qa-qc-preread failed\n")
            return 2 if proc.returncode == 2 else proc.returncode
        sys.stdout.write(proc.stdout)

    outline_path = outdir / "preread_outline.json"
    if not outline_path.is_file():
        sys.stderr.write(f"error: missing preread outline: {outline_path}\n")
        return 2

    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    open_issues = list(outline.get("open_issues") or [])
    arts_present = dict(outline.get("artifacts_present") or {})
    # preread itself
    arts_present["preread"] = (outdir / "preread.md").is_file()

    required = list(stage_cfg.get("required_artifacts") or [])
    missing_required = [a for a in required if not arts_present.get(a)]

    checklist = []
    for a in required:
        checklist.append(
            {
                "artifact": a,
                "required": True,
                "present": bool(arts_present.get(a)),
            }
        )
    for a, present in arts_present.items():
        if a not in required:
            checklist.append({"artifact": a, "required": False, "present": bool(present)})

    p10 = str(contract.get("p10_p90") or outline.get("p10_p90") or "unknown")
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    refused = False
    refuse_reasons: list[str] = []
    if missing_required:
        refused = True
        refuse_reasons.append("missing required artifacts: " + ", ".join(missing_required))
    if mode == "strict" and open_issues:
        refused = True
        refuse_reasons.append(
            f"strict mode: {len(open_issues)} open issue(s) (use --allow-open-issues for draft)"
        )

    status = "REFUSED" if refused else ("DRAFT" if (mode == "draft" and open_issues) else "PASS")

    packet = {
        "skill": "gate-packet",
        "stage": stage,
        "mode": mode,
        "status": status,
        "p10_p90": p10,
        "contract": str(contract_label),
        "profile": contract.profile_name,
        "generated_at": generated_at,
        "stage_description": stage_cfg.get("description", ""),
        "required_artifacts": required,
        "checklist": checklist,
        "missing_required_artifacts": missing_required,
        "open_issues_count": len(open_issues),
        "open_issues": open_issues,
        "refuse_reasons": refuse_reasons,
        "preread_md": str(outdir / "preread.md"),
        "preread_outline_json": str(outline_path),
        "outputs": {
            "gate_packet_json": str(outdir / "gate_packet.json"),
            "gate_packet_md": str(outdir / "gate_packet.md"),
        },
    }

    (outdir / "gate_packet.json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        f"# Gate packet — {stage}",
        "",
        f"- Status: **{status}**",
        f"- Mode: `{mode}`",
        f"- **P10/P90: {p10}**",
        f"- Generated: `{generated_at}`",
        f"- Contract: `{contract_label}`",
        f"- Open issues: {len(open_issues)}",
        "",
        "## Checklist",
        "",
        "| Artifact | Required | Present |",
        "|---|---|---|",
    ]
    for row in checklist:
        md_lines.append(
            f"| {row['artifact']} | {'yes' if row['required'] else 'no'} | "
            f"{'yes' if row['present'] else 'NO'} |"
        )
    md_lines.append("")
    md_lines.append("## Refuse reasons")
    md_lines.append("")
    if refuse_reasons:
        for r in refuse_reasons:
            md_lines.append(f"- {r}")
    else:
        md_lines.append("_None._")
    md_lines.append("")
    md_lines.append("## Open issues (from pre-read)")
    md_lines.append("")
    if not open_issues:
        md_lines.append("_None._")
    else:
        for i, issue in enumerate(open_issues, 1):
            md_lines.append(f"{i}. **[{issue.get('severity')}]** `{issue.get('id')}`: {issue.get('detail')}")
    md_lines.append("")
    md_lines.append("## Pre-read")
    md_lines.append("")
    md_lines.append(f"- Markdown: `{outdir / 'preread.md'}`")
    md_lines.append(f"- Outline: `{outline_path}`")
    md_lines.append(f"- HTML: `{outdir / 'preread.html'}`")
    md_lines.append("")
    (outdir / "gate_packet.md").write_text("\n".join(md_lines), encoding="utf-8")

    sys.stdout.write(
        f"gate-packet: status={status} mode={mode} stage={stage} "
        f"open_issues={len(open_issues)} missing_required={missing_required}\n"
        f"  wrote {outdir / 'gate_packet.json'}\n"
    )

    if refused:
        for r in refuse_reasons:
            sys.stderr.write(f"refuse: {r}\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: {exc}\n")
        raise SystemExit(1) from exc
