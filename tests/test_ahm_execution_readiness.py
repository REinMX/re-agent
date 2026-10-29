"""Preflight weight guards using complete, independently authored tiny inputs."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/ahm-workflow/scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def screened_case(tmp_path, capsys):
    pre = load_script("ahm_preflight")
    screen = load_script("screen_params")
    prior = tmp_path / "prior.dist"
    prior.write_text("A UNIFORM 0 1\n")
    summary = tmp_path / "observations.txt"
    summary.write_text(
        "SUMMARY_OBSERVATION O1 {\n"
        "  VALUE = 10; ERROR = 2; DATE = 01/01/2001; KEY = FOPT;\n"
        "};\n"
        "SUMMARY_OBSERVATION O2 {\n"
        "  VALUE = 20; ERROR = 4; DATE = 01/01/2001; KEY = FWPT;\n"
        "};\n"
    )
    config = tmp_path / "case.ert"
    config.write_text(
        "NUM_REALIZATIONS 100\nRANDOM_SEED 1\nGEN_KW G prior.dist\nOBS_CONFIG observations.txt\n"
    )
    inventory = tmp_path / "observations.csv"
    inventory.write_text("OBS,OBSERVED,ERROR,KEY,DATE\nO1,10,2,FOPT,2001-01-01\nO2,20,4,FWPT,2001-01-01\n")
    design = tmp_path / "design.csv"
    design.write_text("REAL,SENSNAME,SENSCASE,A\n0,A,low,0\n1,A,high,1\n")
    residuals = tmp_path / "residuals.csv"
    with residuals.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ("ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED", "STATUS", "RESIDUAL")
        )
        for real, values, status in (
            (0, (10, 20), "exact"),
            (1, (22, 36), "interpolated"),
        ):
            for obs, key, observed, error, simulated in zip(
                ("O1", "O2"), ("FOPT", "FWPT"), (10, 20), (2, 4), values, strict=True
            ):
                writer.writerow(
                    (
                        "screen",
                        real,
                        obs,
                        key,
                        "2001-01-01",
                        observed,
                        error,
                        simulated,
                        status,
                        (simulated - observed) / error,
                    )
                )
    output = tmp_path / "screening"
    status = screen.main(
        [
            "--design",
            str(design),
            "--residuals",
            str(residuals),
            "--observations",
            str(inventory),
            "--dist",
            str(prior),
            "--no-ahm-analysis",
            "tiny weight validation regression; no ensemble executed",
            "--out",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    assert status == 0, captured.err
    assert captured.err == ""
    screening = output / "screening.json"
    record = json.loads(screening.read_text())
    assert record["kept"] == ["A"]
    assert record["domain"]["observations_authoritative"] is True
    assert record["domain"]["observations"] == ["O1", "O2"]
    return pre, config, screening


@pytest.mark.parametrize(
    "weights",
    [
        pytest.param("-1,2,1", id="negative"),
        pytest.param("nan,2,1", id="nan"),
        pytest.param("inf,2,1", id="positive-infinity"),
        pytest.param("-inf,2,1", id="negative-infinity"),
        pytest.param("not-a-weight", id="nonnumeric"),
        pytest.param("0,0,0", id="all-zero"),
    ],
)
def test_preflight_refuses_malformed_weights(screened_case, weights):
    pre, config, screening = screened_case

    result = pre.check(
        argv=("ert", "es_mda", config.name, "--weights", weights), cwd=config.parent, screening=screening
    )

    assert result.ready is False, f"malformed weights {weights!r} were accepted: {result}"
    assert result.command is None, f"malformed weights {weights!r} produced {result.command!r}"
    assert any(item.severity == "error" and "weight" in item.message.lower() for item in result.items), (
        result.items
    )


def test_preflight_accepts_relative_weights_without_renormalizing_command(screened_case):
    pre, config, screening = screened_case

    result = pre.check(
        argv=("ert", "es_mda", config.name, "--weights", "4,2,1"), cwd=config.parent, screening=screening
    )

    assert result.ready is True, result.items
    assert result.sampled == ("A",)
    assert result.members == 100
    assert result.command == ("ert es_mda case.ert --weights 4,2,1")
    assert not any(item.severity == "error" for item in result.items)


@pytest.mark.parametrize(
    ("weights", "inverse", "rescaled"),
    [("4,2", "0.75", "3, 1.5"), ("4,2,1", "1.75", "7, 3.5, 1.75")],
)
def test_preflight_notes_the_weights_ert_runs_with(screened_case, weights, inverse, rescaled):
    pre, config, screening = screened_case

    result = pre.check(
        argv=("ert", "es_mda", config.name, "--weights", weights), cwd=config.parent, screening=screening
    )

    assert result.ready is True, result.items
    assert result.command == f"ert es_mda case.ert --weights {weights}"
    notes = [item.message for item in result.items if item.code == "AHM-PRE-WEIGHTS"]
    assert [item.severity for item in result.items if item.code == "AHM-PRE-WEIGHTS"] == ["note"], (
        result.items
    )
    assert (
        f"sum(1/w) = {inverse}. ERT rescales them to w * sum(1/w) = {rescaled} and runs with those"
        in notes[0]
    )


def test_changed_observation_file_cannot_reuse_screening(screened_case):
    pre, config, screening = screened_case
    obs = config.parent / "observations.txt"
    obs.write_text(obs.read_text().replace("VALUE = 10", "VALUE = 11"))

    result = pre.check(argv=("ert", "ensemble_smoother", config.name), cwd=config.parent, screening=screening)

    assert result.ready is False
    assert result.command is None
    assert any(item.code == "AHM-PRE-OBS" and item.severity == "error" for item in result.items)


def test_missing_observation_source_refuses(screened_case):
    pre, config, screening = screened_case
    config.write_text(config.read_text().replace("observations.txt", "missing.obs"))

    result = pre.check(argv=("ert", "ensemble_smoother", config.name), cwd=config.parent, screening=screening)

    assert result.ready is False
    assert result.command is None
    assert any("missing.obs" in item.message for item in result.items if item.severity == "error")
