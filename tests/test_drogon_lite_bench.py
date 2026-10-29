"""drogon-lite bench: the AHM skills graded on the right answer against a synthetic truth.

Three levels:
- fast (always): the grading rules, on the measured drogon-lite numbers.
- existing runs (DROGON_LITE_RUNS=<folder with base_* and screened_* run folders>): grade finished runs.
- full ES-MDA (marker drogonlite, RUN_DROGONLITE=1, make check-drogonlite): clone drogon-lite, make the truth, run
  ES-MDA with 100 members, and grade. About 10 minutes.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("drogon_lite_bench_t", ROOT / "tools/drogon_lite_bench.py")
bench = importlib.util.module_from_spec(spec)
sys.modules["drogon_lite_bench_t"] = bench
spec.loader.exec_module(bench)


def failed(criteria) -> set[str]:
    return {c.name for c in criteria if not c.passed}


# measured on drogon-lite, seed 1001, 100 members, ES-MDA 4,2,1
BASE = ("compensating", (2, 2), (0.87, 0.37, 0.81), [], (False, ["AHM-PRE-UNSCREENED"]))
SCREENED = ("plausible", (0, 0), (0.93, 0.58, 0.99), [], (True, []))


def test_right_answers_pass():
    assert not failed(bench.judge("compensating", "", *BASE))
    assert not failed(bench.judge("plausible", "", *SCREENED))


def test_ungraded_edges_cannot_pass_without_a_truth_covered_edge():
    criteria = bench.judge("compensating", "", "compensating", (0, 0), (0.9, 0.4, 0.9), [], unknown_edges=1)
    assert "edge_flags_are_real" in failed(criteria)
    edge = next(c for c in criteria if c.name == "edge_flags_are_real")
    assert "1 ungraded EDGE" in edge.measured


def test_kvkh_exemption_does_not_change_the_measured_base_grading():
    assert not failed(bench.judge("compensating", "", *BASE, unknown_edges=1))


def test_a_good_fit_called_plausible_fails_the_base():
    verdict, *rest = BASE
    assert failed(bench.judge("compensating", "", "plausible", *rest)) == {"compensation_verdict"}


def test_the_biased_coverage_fails():
    """The old 0.45 (noise-biased) quoted as the coverage, against a predictive 0.40."""
    assert "coverage_unbiased" in failed(
        bench.judge("plausible", "", "plausible", (0, 0), (0.40, 0.45, 0.9), [])
    )
    assert "coverage_calibrated" in failed(
        bench.judge("plausible", "", "plausible", (0, 0), (0.70, 0.45, 0.9), [])
    )


def test_edge_flags_on_the_truth_fail_and_the_gate_must_refuse_the_base():
    assert "edge_flags_are_real" in failed(
        bench.judge("compensating", "", "compensating", (0, 2), (0.9, 0.4, None), [])
    )
    assert "preflight_gate" in failed(bench.judge("compensating", "", *BASE[:4], (True, [])))
    assert "physics_complete" in failed(
        bench.judge("plausible", "", "plausible", (0, 0), (0.9, 0.5, 0.9), ["PHYS-AQUIFER"])
    )


MIXED_OBS = """
SUMMARY_OBSERVATION WOPR_P1_0 { VALUE = 100.0; ERROR = 10.0; DATE = 2021-01-25; KEY = WOPR:P1; };
SUMMARY_OBSERVATION WWCT_P1_0 { VALUE = 0.2; ERROR = 0.05; DATE = 2021-01-25; KEY = WWCT:P1; };
RFT_OBSERVATION RFT_P3_0 { WELL = P3; PROPERTY = PRESSURE; DATE = 2021-01-25; VALUE = 166.8; ERROR = 1.0;
    EAST = 925.0; NORTH = 625.0; TVD = 1985.0; };
BREAKTHROUGH_OBSERVATION BT_P1 { KEY = WWCT:P1; DATE = 2021-07-24; ERROR = 30; THRESHOLD = 0.1; };
"""


def test_mixed_observation_file_grades_summary_and_lists_the_skipped(tmp_path):
    """The current drogon-lite obs file has RFT and breakthrough observations next to the summary ones."""
    obs = tmp_path / "mixed.obs"
    obs.write_text(MIXED_OBS)
    obs_qc = bench._load("obs_qc", bench.SKILLS / "history-match-qc" / "scripts")
    graded, skipped = bench.configured_observations(obs_qc, obs)
    assert graded == {"WOPR_P1_0", "WWCT_P1_0"}
    assert [(s["name"], s["kind"]) for s in skipped] == [
        ("RFT_P3_0", "RFT_OBSERVATION"),
        ("BT_P1", "BREAKTHROUGH_OBSERVATION"),
    ]
    assert all("not graded" in s["reason"] for s in skipped)


def test_an_obs_file_without_summary_observations_is_refused(tmp_path):
    obs = tmp_path / "rft_only.obs"
    obs.write_text("\n".join(line for line in MIXED_OBS.splitlines() if "SUMMARY_OBSERVATION" not in line))
    obs_qc = bench._load("obs_qc", bench.SKILLS / "history-match-qc" / "scripts")
    with pytest.raises(ValueError, match="no SUMMARY_OBSERVATION"):
        bench.configured_observations(obs_qc, obs)


RUNS = os.environ.get("DROGON_LITE_RUNS")


@pytest.mark.skipif(not RUNS, reason="set DROGON_LITE_RUNS to a folder of finished drogon-lite runs")
@pytest.mark.parametrize(("setup", "expect"), [("base", "compensating"), ("screened", "plausible")])
def test_existing_runs(tmp_path, setup, expect):
    cases = sorted(Path(RUNS).glob(f"{setup}_*"))
    if not cases:
        pytest.skip(f"no {setup}_* run in {RUNS}")
    for case in cases:
        result = bench.grade(case, 3, expect, tmp_path / case.name)
        assert result["passed"], (case.name, result["criteria"])


@pytest.mark.drogonlite
def test_full_esmda(tmp_path):
    source = Path(os.environ.get("DROGON_LITE", ""))
    venv = os.environ.get("DROGON_LITE_VENV", "")
    assert (source / "Makefile").is_file() and venv, (
        "set DROGON_LITE=<drogon-lite clone> DROGON_LITE_VENV=<venv>"
    )
    # DROGON_LITE_WORK: a work folder that Flow can see, when Flow runs in a container that mounts only some paths
    work = Path(os.environ.get("DROGON_LITE_WORK") or tmp_path)
    case = work / f"drogon-lite-{tmp_path.name}"
    assert not case.exists(), case
    subprocess.run(
        ["git", "clone", "-q", str(source), str(case)], check=True
    )  # a fresh clone: what a user gets
    for target in ("truth", "esmda"):
        done = subprocess.run(
            ["make", target, f"VENV={venv}"], cwd=case, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, (target, done.stdout[-2000:], done.stderr[-2000:])
    result = bench.grade(case, 3, "plausible", tmp_path / "bench")
    assert result["passed"], result["criteria"]
