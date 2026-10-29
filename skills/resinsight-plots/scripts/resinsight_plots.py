#!/usr/bin/env python3
"""Orchestrate ResInsight CLI plots + matplotlib evidence fallback.

Order:
  1) try_resinsight_cli.py  (profile resinsight.bin/headless + --summaryplot + --savesnapshots)
  2) plot_profiles_evidence.py  (always for P10/P90 ensemble bands in the contract convention;
     also covers contract vectors.plot_required if ResInsight fails)

Plots are evidence only. Tables / scenario-qc gate pass/fail.
Does not start ERT/Flow. Does not delete scratch. Does not push GitHub.

Exit: 0 if required PNGs exist under deliverables/plots (ResInsight and/or evidence).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402


def run_script(py: str, script: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [py, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def non_empty_pngs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.rglob("*.png")) if p.stat().st_size > 0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_contract_args(parser)
    parser.add_argument(
        "--eclbase",
        default=None,
        help="Deck name or eclipse/model/NAME (default: contract eclbase; required)",
    )
    parser.add_argument(
        "--iter", default=None, dest="iteration", help="Runpath iteration folder (default: contract)"
    )
    parser.add_argument("--skip-resinsight", action="store_true")
    parser.add_argument("--skip-evidence", action="store_true")
    parser.add_argument(
        "--multi-reals",
        default="",
        help="Passed to ResInsight multi-case sample (e.g. 0,10,20). Default: none",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(
            args.contract, args.profile, skip_required=("eclbase",) if args.eclbase else ()
        )
        ecl_sub, ecl_name = contract.eclbase(args.eclbase)
        deliverables = contract.path("deliverables")
        convention = contract.p10_p90
        required = contract.plot_required()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    args.eclbase = f"{ecl_sub}/{ecl_name}"
    py = str(contract.get("python") or "")
    if not py or not Path(py).is_file():
        py = sys.executable
    contract_args = ["--contract", str(args.contract)] if args.contract else []
    if contract.profile_name != contract_lib.LEGACY_PROFILE:
        contract_args += ["--profile", contract.profile_name]
    plots_root = deliverables / "plots"
    plots_root.mkdir(parents=True, exist_ok=True)
    gui = str((contract.get("resinsight") or {}).get("gui_launcher") or "")
    gui_hint = f"`{gui}` or " if gui and not contract_lib.has_placeholder(gui) else ""

    ri_rc = None
    ri_out = ""
    if not args.skip_resinsight:
        ri_rc, ri_out = run_script(
            py,
            HERE / "try_resinsight_cli.py",
            [
                *contract_args,
                "--eclbase",
                args.eclbase,
                *(["--iter", args.iteration] if args.iteration else []),
                "--multi-reals",
                args.multi_reals,
                "--outdir",
                str(plots_root / "resinsight"),
            ],
        )
        sys.stdout.write(ri_out)
        if ri_rc != 0:
            sys.stderr.write(
                f"warn: ResInsight CLI path failed (rc={ri_rc}); "
                "matplotlib evidence will cover required plots.\n"
            )

    ev_rc = None
    ev_out = ""
    if not args.skip_evidence:
        # Always produce P10/P90 band figures (ResInsight summaryplot is not bands).
        ev_rc, ev_out = run_script(
            py,
            HERE / "plot_profiles_evidence.py",
            [
                *contract_args,
                "--outdir",
                str(plots_root / "evidence"),
            ],
        )
        sys.stdout.write(ev_out)
        if ev_rc != 0:
            sys.stderr.write(f"error: evidence plots failed rc={ev_rc}\n")
            return 2

    ri_pngs = non_empty_pngs(plots_root / "resinsight")
    ev_pngs = non_empty_pngs(plots_root / "evidence")
    all_pngs = ri_pngs + ev_pngs

    # Required coverage: contract plot_required (+ WOPR:<wells.plot_default>) present in file names
    def covers(keys: list[str], paths: list[Path]) -> bool:
        names = " ".join(p.name.upper() for p in paths)
        for k in keys:
            token = k.replace(":", "_").upper()
            if token in names:
                continue
            if ":" in k:
                fam, ent = (x.upper() for x in k.split(":", 1))
                if fam in names and ent in names:
                    continue
            return False
        return True

    ok_cover = covers(required, all_pngs)

    manifest = {
        "skill": "resinsight-plots",
        "p10_p90": convention,
        "resinsight_cli_rc": ri_rc,
        "evidence_rc": ev_rc,
        "resinsight_pngs": [str(p) for p in ri_pngs],
        "evidence_pngs": [str(p) for p in ev_pngs],
        "required_vectors": required,
        "coverage_ok": ok_cover,
        "gate_note": "Plots are evidence only. Tables and scenario-qc decide pass/fail.",
        "gui_hint": (
            "Interactive ResInsight GUI (needs DISPLAY): "
            f"{gui_hint}ResInsight --case <SMSPEC_or_EGRID>. "
            "Headless mode comes from the contract profile (resinsight.headless)."
        ),
    }
    (plots_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    readme = plots_root / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Plots (evidence only)",
                "",
                f"P10/P90 convention: **{convention}** on matplotlib band figures "
                + ("(low=P10, high=P90)." if convention == "numerical" else "(P10 = upside)."),
                "",
                "- `resinsight/` — ResInsight CLI `--summaryplot` snapshots (when the CLI works).",
                "- `evidence/` — matplotlib from deliverables profiles/bands; labeled **NOT ResInsight**.",
                "",
                "QC/tables (`ensemble-bands`, `scenario-qc`) gate pass/fail. Plots do not.",
                "",
                f"GUI: {gui_hint}`ResInsight --case <path-to-SMSPEC-or-EGRID>`.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "plots_root": str(plots_root),
                "n_resinsight": len(ri_pngs),
                "n_evidence": len(ev_pngs),
                "coverage_ok": ok_cover,
                "resinsight_cli_rc": ri_rc,
            },
            indent=2,
        )
    )

    if not all_pngs:
        sys.stderr.write("error: no non-empty PNGs written\n")
        return 2
    if not ok_cover:
        sys.stderr.write(f"error: required plot coverage missing: {required}\n")
        return 2
    # Prefer ResInsight success, but evidence-only is OK if RI failed
    if args.skip_evidence and (ri_rc or 1) != 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
