"""Screening CLI integrity regressions using synthetic tables, never a simulator."""

import csv
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills/ahm-workflow/scripts"
RESIDUAL_COLUMNS = (
    "ENSEMBLE",
    "REAL",
    "OBS",
    "KEY",
    "DATE",
    "OBSERVED",
    "ERROR",
    "SIMULATED",
    "STATUS",
    "RESIDUAL",
)


def write_table(path, columns, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def cli(script, *args):
    env = {name: value for name, value in os.environ.items() if name not in {"PYTHONPATH", "PYTHONHOME"}}
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def case(tmp_path):
    design = [
        {"REAL": 0, "SENSNAME": "A", "SENSCASE": "low", "A": 0.0},
        {"REAL": 1, "SENSNAME": "A", "SENSCASE": "high", "A": 1.0},
    ]
    rows = []
    for real, simulated, status in ((0, (10.0, 20.0), "exact"), (1, (22.0, 36.0), "interpolated")):
        for obs, key, observed, error, value in zip(
            ("O1", "O2"), ("FOPT", "FWPT"), (10.0, 20.0), (2.0, 4.0), simulated, strict=True
        ):
            rows.append(
                {
                    "ENSEMBLE": "screen",
                    "REAL": real,
                    "OBS": obs,
                    "KEY": key,
                    "DATE": "2001-01-01",
                    "OBSERVED": observed,
                    "ERROR": error,
                    "SIMULATED": value,
                    "STATUS": status,
                    "RESIDUAL": (value - observed) / error,
                }
            )
    prior = tmp_path / "prior.dist"
    prior.write_text("A UNIFORM 0 1\n")
    config = tmp_path / "case.ert"
    config.write_text("NUM_REALIZATIONS 100\nRANDOM_SEED 1\nGEN_KW G prior.dist\n")
    observations = [
        {"OBS": "O1", "OBSERVED": 10.0, "ERROR": 2.0, "KEY": "FOPT", "DATE": "2001-01-01"},
        {"OBS": "O2", "OBSERVED": 20.0, "ERROR": 4.0, "KEY": "FWPT", "DATE": "2001-01-01"},
    ]
    return SimpleNamespace(
        root=tmp_path, design=design, rows=rows, prior=prior, config=config, observations=observations
    )


def run_screen(case, output="screening", *, observations=True, extra=()):
    design = case.root / "design.csv"
    residuals = case.root / "residuals.csv"
    write_table(design, ("REAL", "SENSNAME", "SENSCASE", "A"), case.design)
    write_table(residuals, RESIDUAL_COLUMNS, case.rows)
    inventory = case.root / "observations.csv"
    write_table(inventory, ("OBS", "OBSERVED", "ERROR", "KEY", "DATE"), case.observations)
    return cli(
        "screen_params.py",
        "--design",
        design,
        "--residuals",
        residuals,
        "--dist",
        case.prior,
        "--no-ahm-analysis",
        "synthetic CLI regression; no prior ensemble run",
        "--out",
        case.root / output,
        *(["--observations", inventory] if observations else []),
        *extra,
    )


def assert_rejected(case, result, diagnostic):
    assert result.returncode != 0, (
        f"screening accepted invalid input and returned {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert re.search(diagnostic, result.stderr, re.IGNORECASE), result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert not (case.root / "screening").exists(), "invalid input created screening output"


def screening_record(case):
    result = run_screen(case)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads((case.root / "screening/screening.json").read_text())


def run_preflight(case):
    return cli(
        "ahm_preflight.py",
        case.config,
        "--screening",
        case.root / "screening/screening.json",
        "--weights",
        "4,2,1",
        "--json",
    )


def test_complete_screening_preserves_measured_numerics_and_member_specific_status(case):
    record = screening_record(case)
    assert record["observations"] == 2
    assert record["kept"] == ["A"]
    assert record["dropped"] == []
    assert len(record["parameters"]) == 1
    parameter = record["parameters"][0]
    assert parameter["name"] == "A"
    assert parameter["low"] == 0.0 and parameter["high"] == 1.0
    assert parameter["chi2_swing"] == pytest.approx(52.0)
    assert parameter["sd_ratio_lin"] == pytest.approx(math.sqrt(3) / 4)
    assert parameter["keep"] is True
    assert parameter["partner"] is None and parameter["cosine"] is None
    assert parameter["reasons"] == []
    assert (case.root / "screening/screening.md").is_file()

    case.rows.reverse()
    result = run_screen(case, "reordered")
    assert result.returncode == 0, result.stderr
    reordered = json.loads((case.root / "reordered/screening.json").read_text())
    assert reordered["parameters"] == record["parameters"]
    assert reordered["observations"] == record["observations"]
    assert reordered["kept"] == record["kept"]
    assert reordered["dropped"] == record["dropped"]


def test_screening_refuses_a_missing_member_observation_pair(case):
    case.rows = [row for row in case.rows if (row["REAL"], row["OBS"]) != (1, "O2")]
    assert_rejected(case, run_screen(case), r"missing|incomplete|coverage|simulated")


def test_screening_refuses_a_missing_simulated_value(case):
    case.rows[-1]["SIMULATED"] = ""
    case.rows[-1]["STATUS"] = "outside"
    case.rows[-1]["RESIDUAL"] = ""
    assert_rejected(case, run_screen(case), r"missing|incomplete|finite|simulated")


def test_screening_refuses_a_whole_missing_endpoint_member(case):
    case.rows = [row for row in case.rows if row["REAL"] != 1]
    assert_rejected(case, run_screen(case), r"realization|member|missing|incomplete")


def test_screening_requires_every_design_member_even_when_not_an_endpoint(case):
    case.design.append({"REAL": 2, "SENSNAME": "base", "SENSCASE": "ref", "A": 0.5})
    assert_rejected(case, run_screen(case), r"realization|member|missing|incomplete|coverage")


def test_screening_refuses_conflicting_duplicate_member_observation(case):
    duplicate = dict(case.rows[0], SIMULATED=999.0, RESIDUAL=494.5)
    case.rows.append(duplicate)
    assert_rejected(case, run_screen(case), r"duplicate|conflict|unique")


@pytest.mark.parametrize("column", ["SIMULATED", "ERROR"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_screening_refuses_nonfinite_simulations_and_errors(case, column, value):
    for row in case.rows:
        if row["OBS"] == "O2":
            row[column] = value
    assert_rejected(case, run_screen(case), rf"{column}|finite")


@pytest.mark.parametrize("value", [0.0, -2.0])
def test_screening_requires_strictly_positive_errors(case, value):
    for row in case.rows:
        if row["OBS"] == "O1":
            row["ERROR"] = value
    assert_rejected(case, run_screen(case), r"error|positive")


def test_screening_refuses_fractional_design_real_instead_of_truncating(case):
    case.design[0]["REAL"] = 0.5
    assert_rejected(case, run_screen(case), r"real|integer|integral")


def test_screening_refuses_negative_design_real_even_with_matching_residuals(case):
    case.design[0]["REAL"] = -1
    for row in case.rows:
        if row["REAL"] == 0:
            row["REAL"] = -1
    assert_rejected(case, run_screen(case), r"real|non.?negative")


def test_screening_refuses_duplicate_design_real(case):
    case.design.append(dict(case.design[0]))
    assert_rejected(case, run_screen(case), r"real|duplicate|unique")


@pytest.mark.parametrize(
    ("column", "value"),
    [("ERROR", 8.0), ("OBSERVED", 999.0), ("KEY", "FOPR"), ("DATE", "2002-01-01")],
)
def test_screening_requires_consistent_observation_metadata_across_members(case, column, value):
    case.rows[-1][column] = value
    assert_rejected(case, run_screen(case), rf"{column}|metadata|inconsistent|conflict")


def test_preflight_accepts_complete_screening_with_the_same_prior(case):
    screening_record(case)
    result = run_preflight(case)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is True
    assert report["sampled"] == ["A"]
    assert "ert es_mda case.ert" in report["command"]
    assert "--weights 4,2,1" in report["command"]
    assert report["selected_members"] == list(range(100))


def test_preflight_refuses_a_changed_sampled_prior_after_screening(case):
    screening_record(case)
    case.prior.write_text("A UNIFORM 0 2\n")
    result = run_preflight(case)
    assert result.returncode != 0, f"stale screening was accepted:\n{result.stdout}\n{result.stderr}"
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert report["command"] is None
    assert any(
        item["severity"] == "error"
        and re.search(r"prior|distribution|screen", item["message"], re.IGNORECASE)
        for item in report["items"]
    ), report["items"]


def test_preflight_allows_a_dropped_parameter_to_be_fixed_at_const(case):
    for row in case.rows:
        if row["REAL"] == 1:
            row["SIMULATED"] = row["OBSERVED"] + row["ERROR"] * 0.01
            row["RESIDUAL"] = 0.01
    record = screening_record(case)
    assert record["dropped"] == ["A"] and record["kept"] == []
    case.prior.write_text("A CONST 0.5\n")
    result = run_preflight(case)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is True
    assert report["sampled"] == []
    assert report["command"] is not None
