#!/usr/bin/env python3
"""Run the history-match QC chain with the skill scripts. Read-only on the case.

  qc_chain.py setup --out <dir>   before the run: prior, observations (with one simulated case), config,
                                  and the ahm_preflight gate (it refuses a run without a screening record)
  qc_chain.py post  --out <dir>   after the run: update log, misfit, predictive coverage, compensation check,
                                  prior vs posterior, failed realizations, and the held-out score when
                                  qc.yml sets heldout_split

Each step writes <out>/<step>.txt with the command, the output, and the exit code. <out>/summary.json lists
the exit codes and, under skipped, each step that did not run with its reason. The chain runs every step and
exits 1 when a step fails. <out> must be a new or empty folder outside the case runpaths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    qc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("screening", "random_seed"):
        if not qc.get(key):
            raise SystemExit(f"{path}: {key} is missing; render the template again")
    for key in ("config", "dist", "obs", "update_log", "screening"):
        qc[key] = HERE / qc[key]
    for key in ("truth", "heldout_split"):
        if qc.get(key):
            qc[key] = HERE / qc[key]
    qc["skills"] = Path(qc["agent_root"]) / "skills"
    qc["case_dir"] = Path(qc["case_dir"])
    qc["last"] = len([w for w in str(qc["weights"]).split(",") if w.strip()])
    return qc


def steps(stage: str, qc: dict, out: Path) -> list[tuple[str, list[str]]]:
    s, case, n = qc["skills"], qc["case_dir"], str(qc["realizations"])
    hm = s / "history-match-qc" / "scripts"
    truth = ["--truth", str(qc["truth"])] if qc.get("truth") else []
    if stage == "setup":
        data = case / "realization-0" / "iter-0" / (qc["eclbase"].replace("<IENS>", "0") + ".DATA")
        return [
            ("prior_qc", [str(hm / "prior_qc.py"), str(qc["dist"]), "--realizations", n, *truth]),
            ("obs_qc", [str(hm / "obs_qc.py"), str(qc["obs"]), "--case", str(data)]),
            (
                "check_ert_config",
                [
                    str(s / "ert-config-author-lint" / "scripts" / "check_ert_config.py"),
                    "--ahm",
                    "--weights",
                    str(qc["weights"]),
                    str(qc["config"]),
                ],
            ),
            (
                "ahm_preflight",
                [
                    str(s / "ahm-workflow" / "scripts" / "ahm_preflight.py"),
                    str(qc["config"]),
                    "--screening",
                    str(qc["screening"]),
                    "--weights",
                    str(qc["weights"]),
                    *(
                        ["--allow-members", str(qc["allow_members_reason"])]
                        if qc.get("allow_members_reason")
                        else []
                    ),
                ],
            ),
        ]
    last = str(qc["last"])
    prior = str(case / "realization-*" / "iter-0")
    posterior = str(case / "realization-*" / f"iter-{last}")
    chain = [
        ("update_log_report", [str(hm / "update_log_report.py"), str(qc["update_log"])]),
        (
            "misfit_report",
            [
                str(hm / "misfit_report.py"),
                "--obs",
                str(qc["obs"]),
                "--prior",
                prior,
                "--posterior",
                posterior,
                "--expected",
                n,
                "--update-log",
                str(qc["update_log"]),
                "--outdir",
                str(out / "misfit"),
            ],
        ),
        (
            "coverage",
            [
                str(hm / "coverage.py"),
                "--residuals",
                str(out / "misfit" / "residuals.csv"),
                "--ensemble",
                "posterior",
            ],
        ),
        (
            "compensation_check",
            [
                str(hm / "compensation_check.py"),
                str(qc["dist"]),
                "--prior-runpaths",
                prior,
                "--posterior-runpaths",
                posterior,
                "--seed",
                str(qc["random_seed"]),
                "--expected",
                n,
                "--misfit-manifest",
                str(out / "misfit" / "manifest.json"),
                *truth,
            ],
        ),
        (
            "prior_posterior",
            [
                str(hm / "prior_qc.py"),
                str(qc["dist"]),
                *truth,
                "--posterior-runpaths",
                posterior,
                "--realizations",
                n,
            ],
        ),
        (
            "triage_runpath",
            [
                str(s / "ert-diagnose" / "scripts" / "triage_runpath.py"),
                str(case),
                "--iter",
                last,
                "--expected",
                n,
            ],
        ),
    ]
    if qc.get("heldout_split"):
        chain.append(
            (
                "heldout_score",
                [
                    str(hm / "heldout_score.py"),
                    "--split",
                    str(qc["heldout_split"]),
                    "--out",
                    str(out / "heldout"),
                ],
            )
        )
    return chain


def skipped(stage: str, qc: dict) -> dict[str, str]:
    if stage == "post" and not qc.get("heldout_split"):
        return {"heldout_score": "heldout_split is null in qc.yml; no held-out score was computed"}
    return {}


def run(stage: str, qc: dict, out: Path) -> dict[str, int]:
    out = out.resolve()
    if out.is_relative_to(qc["case_dir"].resolve()):
        raise SystemExit(f"--out {out} is inside the case runpaths; choose a folder outside them")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"--out {out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    codes = {}
    for name, argv in steps(stage, qc, out):
        command = [sys.executable, *argv]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        (out / f"{name}.txt").write_text(
            "$ " + " ".join(command) + "\n" + result.stdout + result.stderr + f"exit={result.returncode}\n",
            encoding="utf-8",
        )
        codes[name] = result.returncode
        print(f"{name}: exit {result.returncode}")
    reasons = skipped(stage, qc)
    for name, reason in reasons.items():
        (out / f"{name}.txt").write_text(f"skipped: {reason}\n", encoding="utf-8")
        print(f"{name}: skipped ({reason})")
    (out / "summary.json").write_text(
        json.dumps({"stage": stage, "exit_codes": codes, "skipped": reasons}, indent=2) + "\n"
    )
    return codes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=("setup", "post"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--qc", type=Path, default=HERE / "qc.yml")
    args = ap.parse_args(argv)
    codes = run(args.stage, load(args.qc), args.out)
    return 1 if any(codes.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
