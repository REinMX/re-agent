"""truth_score.py on analytic inputs. No simulator or project data."""

import csv
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/history-match-qc/scripts/truth_score.py"
COLUMNS = ["ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED", "STATUS"]
OFFSETS = (-1.0, 0.0, 1.0)


def load():
    spec = importlib.util.spec_from_file_location("tested_truth_score", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def residual_rows(label, centres, offsets=OFFSETS):
    """Member i of observation k simulates OBSERVED + ERROR * (centre_k + offset_i). The offsets sum to 0."""
    rows = []
    for k, centre in enumerate(centres):
        observed, error = 100.0 * (k + 1), 2.0 + k
        for real, offset in enumerate(offsets):
            rows.append(
                {
                    "ENSEMBLE": label,
                    "REAL": real,
                    "OBS": f"O{k}",
                    "KEY": f"WOPR:P{k}",
                    "DATE": "2020-01-01",
                    "OBSERVED": observed,
                    "ERROR": error,
                    "SIMULATED": observed + error * (centre + offset),
                    "STATUS": "exact",
                }
            )
    return rows


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def member_rms(centres, offset):
    return math.sqrt(sum((c + offset) ** 2 for c in centres) / len(centres))


def test_response_rms_has_the_analytic_values(tmp_path):
    score = load()
    path = write_csv(
        tmp_path / "r.csv", residual_rows("prior", (3.0, 4.0)) + residual_rows("posterior", (0.3, 0.4))
    )
    result = score.response_score(score.read_residuals(path), "prior", "posterior")
    assert result["prior"]["mean_response_rms"] == pytest.approx(math.sqrt(12.5), rel=1e-12)
    assert result["posterior"]["mean_response_rms"] == pytest.approx(math.sqrt(0.125), rel=1e-12)
    assert result["ratio"] == pytest.approx(0.1, rel=1e-12)
    expected = sum(member_rms((3.0, 4.0), d) for d in OFFSETS) / 3
    assert result["prior"]["member_mean_rms"] == pytest.approx(expected, rel=1e-12)
    assert (result["prior"]["observations"], result["prior"]["members"]) == (2, 3)


def test_excluded_observations_leave_the_rms(tmp_path):
    score = load()
    rows = residual_rows("prior", (3.0, 4.0, 50.0)) + residual_rows("posterior", (0.3, 0.4, 50.0))
    table = score.read_residuals(write_csv(tmp_path / "r.csv", rows))
    result = score.response_score(table, "prior", "posterior", exclude={"O2"})
    assert result["ratio"] == pytest.approx(0.1, rel=1e-12)
    assert result["excluded"] == ["O2"]


def test_interval_uses_numerical_percentiles(tmp_path):
    score = load()
    rows = [
        {
            "ENSEMBLE": "posterior",
            "REAL": i,
            "OBS": "FOPT_END",
            "KEY": "FOPT",
            "DATE": "2030-01-01",
            "OBSERVED": 85.5,
            "ERROR": 1.0,
            "SIMULATED": float(i),
            "STATUS": "exact",
        }
        for i in range(101)
    ]
    table = score.read_residuals(write_csv(tmp_path / "r.csv", rows))
    result = score.interval(table, "posterior", "FOPT_END")
    assert (result["p10"], result["p90"]) == (pytest.approx(10.0), pytest.approx(90.0))
    assert result["truth"] == 85.5 and result["truth_inside"] is True
    assert score.interval(table.assign(OBSERVED=95.0), "posterior", "FOPT_END")["truth_inside"] is False


def standardized(n=40):
    """n values with mean 0 and sample sd (ddof 1) 1."""
    raw = [math.sin(1.7 * i + 0.3) for i in range(n)]
    mean = math.fsum(raw) / n
    sd = math.sqrt(math.fsum((v - mean) ** 2 for v in raw) / (n - 1))
    return [(v - mean) / sd for v in raw]


def test_parameter_recovery_of_a_known_gaussian_case():
    score = load()
    z = standardized()
    prior = {"G:PERM": [10 ** (0.0 + 0.5 * v) for v in z], "G:OWC": [2000.0 + 10.0 * v for v in z]}
    posterior = {"G:PERM": [10 ** (0.2 + 0.05 * v) for v in z], "G:OWC": [2004.0 + 1.0 * v for v in z]}
    dists = {
        "PERM": {"name": "PERM", "distribution": "LOGUNIF", "arguments": {"min": 0.1, "max": 10}},
        "OWC": {"name": "OWC", "distribution": "UNIFORM", "arguments": {"min": 1980, "max": 2020}},
    }
    rows = score.parameter_score(dists, prior, posterior, {"PERM": 10**0.25, "OWC": 2001.0})
    perm, owc = rows["PERM"], rows["OWC"]
    assert perm["scale"] == "log10" and owc["scale"] == "native"
    assert perm["prior_sd"] == pytest.approx(0.5, rel=1e-12)
    assert perm["sd_reduction"] == pytest.approx(0.9, rel=1e-12)
    assert perm["bias_in_prior_sd"] == pytest.approx(0.05 / 0.5, rel=1e-9)
    assert owc["sd_reduction"] == pytest.approx(0.9, rel=1e-12)
    assert owc["bias_in_prior_sd"] == pytest.approx(3.0 / 10.0, rel=1e-12)
    assert perm["truth_in_posterior_p05_p95"] is True
    assert owc["truth_in_posterior_p05_p95"] is False


def test_constant_parameters_are_skipped_and_a_missing_truth_is_refused():
    score = load()
    z = standardized()
    dists = {
        "A": {"name": "A", "distribution": "NORMAL", "arguments": {"mean": 0, "std": 1}},
        "C": {"name": "C", "distribution": "CONST", "arguments": {"value": 5}},
    }
    values = {"A": z, "C": [5.0] * len(z)}
    assert set(score.parameter_score(dists, values, values, {"A": 0.0})) == {"A"}
    with pytest.raises(ValueError, match="no truth value for A"):
        score.parameter_score(dists, values, values, {"C": 5.0})


def test_refuses_an_incomplete_grid_or_a_changed_observation_set(tmp_path):
    score = load()
    rows = residual_rows("prior", (3.0, 4.0)) + residual_rows("posterior", (0.3, 0.4))
    rows[-1]["STATUS"] = "outside"
    with pytest.raises(ValueError, match="no usable prediction"):
        score.response_score(score.read_residuals(write_csv(tmp_path / "a.csv", rows)), "prior", "posterior")
    rows = residual_rows("prior", (3.0, 4.0)) + residual_rows("posterior", (0.3,))
    with pytest.raises(ValueError, match="observation sets differ"):
        score.response_score(score.read_residuals(write_csv(tmp_path / "b.csv", rows)), "prior", "posterior")


def test_command_line_writes_json_and_gives_no_verdict(tmp_path):
    history = write_csv(
        tmp_path / "h.csv", residual_rows("prior", (3.0, 4.0)) + residual_rows("posterior", (0.3, 0.4))
    )
    forecast_rows = residual_rows("prior", (6.0, 8.0)) + residual_rows("posterior", (3.0, 4.0))
    forecast_rows += [
        {
            "ENSEMBLE": label,
            "REAL": i,
            "OBS": "FOPT_END",
            "KEY": "FOPT",
            "DATE": "2030-01-01",
            "OBSERVED": 1.0,
            "ERROR": 1.0,
            "SIMULATED": float(i),
            "STATUS": "exact",
        }
        for label in ("prior", "posterior")
        for i in range(3)
    ]
    forecast = write_csv(tmp_path / "f.csv", forecast_rows)
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--history",
            str(history),
            "--forecast",
            str(forecast),
            "--interval",
            "FOPT_END",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["history"]["ratio"] == pytest.approx(0.1)
    assert report["forecast"]["ratio"] == pytest.approx(0.5)
    assert report["forecast"]["excluded"] == ["FOPT_END"]
    assert report["interval"]["truth_inside"] is True
    assert "verdict" not in json.dumps(report).lower().replace("no verdict", "")


def test_command_line_refusal_exits_1(tmp_path):
    rows = residual_rows("prior", (3.0, 4.0))
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--history", str(write_csv(tmp_path / "h.csv", rows))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1
    assert "posterior" in run.stderr


def test_help_needs_no_numpy():
    run = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0 and "--forecast" in run.stdout
