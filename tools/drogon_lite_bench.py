#!/usr/bin/env python3
"""Grade the AHM skills on drogon-lite, against its known synthetic truth. Read-only on the case.

drogon-lite (github.com/REinMX/drogon-lite) is a small FMU model with a synthetic truth. A skill passes when it
gets the right answer, not when the misfit is low. For one finished ES or ES-MDA run of the case this runs
misfit_report.py, coverage.py, compensation_check.py, physics_checklist.py and, with --screening,
ahm_preflight.py, and grades:
    compensation_verdict   the verdict is --expect (base setup: compensating; screened setup: plausible)
    edge_flags_are_real    most truth-covered EDGE parameters miss it (P5-P95); ungraded EDGE alone cannot pass
    coverage_unbiased      predictive coverage above the noise-biased share (the 0.45 against 0.85 lesson)
    coverage_calibrated    with --expect plausible: predictive and truth-response coverage >= 0.8
    physics_complete       no missing physics item in the deck and config of the case
    preflight_gate         with --screening: the gate lets a screened config run and refuses an unscreened one

    drogon_lite_bench.py --case <drogon-lite run folder> --last 3 --expect plausible --out <dir> [--screening <json>]
Fail closed: --out must not exist and must not overlap the case; misfit_report.py must exit 0 (no --allow-partial,
its output is logged to <out>/misfit_report.log); the members (NUM_REALIZATIONS) and the SUMMARY_OBSERVATION names
of the case config must be exactly the graded ones; the truth must cover every SUMMARY_OBSERVATION. Other kinds
(RFT_OBSERVATION, BREAKTHROUGH_OBSERVATION, ...) are not graded: bench.json skipped_observations lists each with its
kind, and a NOTE line names them.
Truth must also cover every bounded parameter, except base KVKH when finite KVKH_UPPER and KVKH_LOWER truth
are supplied. KVKH still counts for the verdict and is listed in bench.json truth_missing. edge_flags_are_real
counts truth-covered EDGE parameters and reports ungraded ones; it fails when only ungraded EDGE exist.
Exit code: 0 = every criterion passed, 1 = a criterion failed or an input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import math
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
CONFIG = "ert/model/drogonlite_ahm.ert"
OBS = "ert/input/observations/drogonlite_hist.obs"
TRUTH = "ert/input/observations/drogonlite_truth.json"
DECK = "eclipse/model/DROGONLITE.DATA"


def _load(name: str, folder: Path):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


@dataclass
class Criterion:
    name: str
    passed: bool
    measured: str


def judge(
    expect: str,
    verdict_text: str,
    verdict: str,
    edges_missing_truth: tuple[int, int],
    cov: tuple[float, float, float | None],
    physics_missing: list[str],
    gate: tuple[bool, list[str]] | None = None,
    unknown_edges: int = 0,
) -> list[Criterion]:
    """Grade one run. cov is (predictive, noise-biased, truth-response); gate is (ready, error codes)."""
    miss, n_edges = edges_missing_truth
    predictive, biased, truth = cov
    criteria = [
        Criterion("compensation_verdict", verdict == expect, f"{verdict_text} (expected {expect})"),
        Criterion(
            "edge_flags_are_real",
            2 * miss > n_edges or not (n_edges or unknown_edges),
            f"{miss} of {n_edges} EDGE parameters miss the truth"
            + (f"; {unknown_edges} ungraded EDGE parameters" if unknown_edges else ""),
        ),
        Criterion(
            "coverage_unbiased",
            predictive > biased,
            f"predictive {predictive:.2f}, noise-biased {biased:.2f}",
        ),
        Criterion("physics_complete", not physics_missing, f"missing {physics_missing or 'none'}"),
    ]
    if expect == "plausible":
        t = float("nan") if truth is None else truth
        criteria.append(
            Criterion(
                "coverage_calibrated",
                predictive >= 0.8 and t >= 0.8,
                f"predictive {predictive:.2f}, truth-response {t:.2f}",
            )
        )
    if gate is not None:
        want, (ready, errors) = expect == "plausible", gate
        state = "ready" if ready else f"refused {errors}"
        criteria.append(
            Criterion("preflight_gate", ready == want, f"{state} (expected {'ready' if want else 'refused'})")
        )
    return criteria


def runpaths(case: Path, iteration: int) -> str:
    found = sorted(glob.glob(str(case / "scratch/*/01_drogonlite_ahm")))
    if len(found) != 1:
        raise ValueError(f"{case}: expected one scratch/<user>/01_drogonlite_ahm folder, found {len(found)}")
    return f"{found[0]}/realization-*/iter-{iteration}"


def truth_values(case: Path) -> tuple[dict[str, float], dict[str, float]]:
    data = json.loads((case / TRUTH).read_text())
    params = {k: float(v) for k, v in data.get("parameters", {}).items() if isinstance(v, int | float)}
    params.update({f"MULTFLT_{k}": float(v) for k, v in data.get("fault_segments", {}).items()})
    responses = {o["name"]: float(o["truth"]) for o in data["observations"] if "truth" in o}
    return params, responses


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def configured_members(settings: dict, runpath_lib) -> set[int]:
    """The member set of the ensemble, from NUM_REALIZATIONS as read by the ERT config mapper."""
    value = (settings.get("NUM_REALIZATIONS") or {}).get("value")
    try:
        return runpath_lib.expected_set(int(value), None)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{CONFIG}: NUM_REALIZATIONS must be a positive integer, found {value!r}") from error


def configured_observations(obs_qc, path: Path) -> tuple[set[str], list[dict]]:
    """The graded SUMMARY_OBSERVATION names of the ERT observation config, and the observations it skips.

    misfit_report.py reads only SUMMARY_OBSERVATION here (no --ert-config), so RFT_OBSERVATION,
    BREAKTHROUGH_OBSERVATION and any other kind are not graded. They are returned as skipped, with kind and
    reason, so the caller reports them; they never count as a pass or as zero.
    """
    records = obs_qc.parse_file(path)
    all_names = [r["name"] for r in records]
    if len(set(all_names)) != len(all_names):
        raise ValueError(f"{path}: duplicate observation names")
    names = [r["name"] for r in records if r["kind"] == "SUMMARY_OBSERVATION"]
    if not names:
        raise ValueError(f"{path}: no SUMMARY_OBSERVATION to grade")
    skipped = [
        {
            "name": r["name"],
            "kind": r["kind"],
            "reason": "not graded: the bench grades SUMMARY_OBSERVATION only",
        }
        for r in records
        if r["kind"] != "SUMMARY_OBSERVATION"
    ]
    return set(names), skipped


def check_manifest(path: Path, expected: set[int]) -> None:
    """The misfit_report manifest must cover exactly the expected members in both ensembles, with no issue."""
    data = json.loads(path.read_text())
    ensembles = data.get("ensembles") if isinstance(data, dict) else None
    if not isinstance(ensembles, dict) or set(ensembles) != {"prior", "posterior"}:
        raise ValueError(f"{path}: malformed misfit manifest, need prior and posterior ensembles")
    if data.get("issues") != []:
        raise ValueError(f"{path}: misfit manifest reports issues {data.get('issues')}")
    for label, item in ensembles.items():
        used = item.get("realizations_used") if isinstance(item, dict) else None
        missing = item.get("missing") if isinstance(item, dict) else None
        if used != sorted(expected) or missing != []:
            raise ValueError(
                f"{path}: misfit manifest {label} used {used}, missing {missing}; expected {sorted(expected)}"
            )


def grade(case: Path, last: int, expect: str, out: Path, screening: Path | None = None) -> dict:
    """Grade one finished run. out must not exist; it is created here and must not overlap the case."""
    hm = SKILLS / "history-match-qc" / "scripts"
    coverage, comp = _load("coverage", hm), _load("compensation_check", hm)
    obs_qc = _load("obs_qc", hm)
    pre = _load("ahm_preflight", SKILLS / "ahm-workflow" / "scripts")
    physics = _load("physics_checklist", SKILLS / "deck-scal-pvt-qc" / "scripts")
    import pandas as pd

    case, out = case.resolve(), out.resolve()
    if _overlaps(out, case):
        raise ValueError(f"--out {out} overlaps the case {case}; the bench is read-only on the case")
    prior_glob, post_glob = runpaths(case, 0), runpaths(case, last)
    files, settings = pre.gen_kw_files((case / CONFIG).resolve())
    expected = configured_members(settings, comp.runpath_lib)
    expected_obs, skipped_obs = configured_observations(obs_qc, case / OBS)
    params = {}
    for path in files:
        found, errors = comp.prior_qc.parse_dist(path)
        if errors:
            raise ValueError("; ".join(e["message"] for e in errors))
        params.update({p["name"]: p for p in found})
    truth_params, truth_resp = truth_values(case)
    truth_matched = comp._match(truth_params, params, set(), TRUTH)
    if any(not math.isfinite(v) for v in truth_matched.values()):
        raise ValueError(f"{TRUTH}: truth values must be finite")
    split = comp._match(truth_params, {"KVKH_UPPER": {}, "KVKH_LOWER": {}}, set(), TRUTH)
    exempt = (
        comp._match({"KVKH": None}, params, set(), TRUTH)
        if len(split) == 2 and all(math.isfinite(v) for v in split.values())
        else {}
    )
    no_param_truth = sorted(comp.bounded_parameters(params) - truth_matched.keys() - exempt.keys())
    if no_param_truth:
        raise ValueError(f"{TRUTH}: no truth value for bounded parameters {no_param_truth}")
    no_truth = sorted(expected_obs - truth_resp.keys())
    if no_truth:
        raise ValueError(f"{TRUTH}: no truth value for configured observations {no_truth}")
    out.mkdir(parents=True, exist_ok=False)
    misfit_dir = out / "misfit"
    command = [sys.executable, "-W", "ignore", str(hm / "misfit_report.py"), "--obs", str(case / OBS)]
    command += ["--prior", prior_glob, "--posterior", post_glob, "--outdir", str(misfit_dir)]
    command += ["--expected", str(len(expected))]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    log = out / "misfit_report.log"
    log.write_text(
        f"command: {shlex.join(command)}\nexit code: {done.returncode}\n"
        f"--- stdout ---\n{done.stdout}\n--- stderr ---\n{done.stderr}\n"
    )
    if done.returncode != 0:
        raise ValueError(
            f"misfit_report.py failed with exit code {done.returncode} (log {log}): {done.stderr.strip()[-400:]}"
        )
    check_manifest(misfit_dir / "manifest.json", expected)
    cov = coverage.from_residuals(
        pd.read_csv(misfit_dir / "residuals.csv"),
        "posterior",
        truth_resp,
        expected_members=expected,
        expected_obs=expected_obs,
    )
    fit, basis = comp.fit_from_manifest(
        misfit_dir / "manifest.json", comp.DEFAULT_LIMITS.misfit_limit, expected
    )
    prior = comp.read_ensemble(prior_glob, None)
    post = comp.read_ensemble(post_glob, None)
    run = comp.check_run(
        case.name, params, prior, post, comp.DEFAULT_LIMITS, fit, basis, truth_params, expected=expected
    )
    deck = physics.read_deck(case / DECK)
    ert = physics.read_ert(case / CONFIG)
    items = physics.checklist(deck, ert)
    missing = [i.code for i in items if i.status == physics.Status.MISSING]
    edges = [p for p in run.parameters if p.flag == "EDGE" and p.truth_inside_p5_p95 is not None]
    unknown_edges = sum(p.flag == "EDGE" and p.truth_inside_p5_p95 is None for p in run.parameters)
    gate = None
    if screening is not None:
        config = (case / CONFIG).resolve()
        result = pre.check(
            argv=("ert", "ensemble_smoother", config.name), cwd=config.parent, screening=screening
        )
        gate = (result.ready, [i.code for i in result.items if i.severity == "error"])
    criteria = judge(
        expect,
        f"{run.verdict}, {basis}",
        run.verdict,
        (sum(not p.truth_inside_p5_p95 for p in edges), len(edges)),
        (cov.predictive, cov.noise_biased, cov.truth_response),
        missing,
        gate,
        unknown_edges=unknown_edges,
    )
    return {
        "case": str(case),
        "expect": expect,
        "passed": all(c.passed for c in criteria),
        "criteria": [asdict(c) for c in criteria],
        "compensation": {"verdict": run.verdict, "score": run.score, "findings": run.findings},
        "truth_missing": run.truth_missing,
        "coverage": asdict(cov),
        "members": sorted(expected),
        "observations": sorted(expected_obs),
        "skipped_observations": skipped_obs,
        "misfit_log": str(log),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path, required=True, help="drogon-lite folder with a finished run")
    ap.add_argument("--last", type=int, default=3, help="posterior iteration (ES-MDA 4,2,1: 3; ES: 1)")
    ap.add_argument("--expect", choices=("plausible", "compensating"), required=True)
    ap.add_argument("--screening", type=Path, help="screening.json, to grade the preflight gate")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if not (args.case / CONFIG).is_file():
        print(f"ERROR: {args.case / CONFIG} not found", file=sys.stderr)
        return 2
    try:
        result = grade(args.case.resolve(), args.last, args.expect, args.out, args.screening)
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    (args.out / "bench.json").write_text(json.dumps(result, indent=1) + "\n")
    skipped = result["skipped_observations"]
    if skipped:
        kinds = sorted({s["kind"] for s in skipped})
        names = ", ".join(s["name"] for s in skipped)
        print(f"NOTE: {len(skipped)} observations not graded ({', '.join(kinds)}): {names}")
    for c in result["criteria"]:
        print(f"{'PASS' if c['passed'] else 'FAIL'} {c['name']}: {c['measured']}")
    print(f"BENCH {'passed' if result['passed'] else 'failed'}: {args.case}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
