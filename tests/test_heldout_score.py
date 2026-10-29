"""Portable held-out scoring contracts. No simulator or project data."""

import csv
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/history-match-qc/scripts/heldout_score.py"
COLUMNS = ["ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "UNIT", "OBSERVED", "ERROR", "SIMULATED"]


def load_scorer():
    assert SCRIPT.is_file(), "heldout_score.py command is missing"
    spec = importlib.util.spec_from_file_location("tested_heldout_score", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows, columns=COLUMNS):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def bundle(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    assimilation = folder / "history.obs"
    assimilation.write_text(
        "SUMMARY_OBSERVATION h { KEY = FOPR; DATE = 2020-01-01; VALUE = 1; ERROR = 2; };\n"
    )
    observations = [
        {
            "OBS": "h",
            "KEY": "FOPR",
            "DATE": "2020-01-01",
            "UNIT": "SM3/DAY",
            "OBSERVED": 1.0,
            "ERROR": 2.0,
            "kind": "latent_truth",
        },
        {
            "OBS": "f",
            "KEY": "FOPR",
            "DATE": "2020-03-01",
            "UNIT": "SM3/DAY",
            "OBSERVED": 1.0,
            "ERROR": 2.0,
            "kind": "latent_truth",
        },
    ]
    rows = []
    for ensemble in ("prior", "posterior"):
        for member, simulated in ((0, 0.0), (2, 2.0)):
            for obs in observations:
                rows.append(
                    {
                        "ENSEMBLE": ensemble,
                        "REAL": member,
                        "SIMULATED": simulated,
                        **{key: obs[key] for key in COLUMNS if key in obs},
                    }
                )
    residuals = folder / "evaluation.csv"
    write_csv(residuals, rows)
    manifest = {
        "schema": "ahm-heldout/1",
        "history_end": "2020-02-01",
        "ensembles": {"prior": [0, 2], "posterior": [0, 2]},
        "assimilation": {"path": "history.obs", "sha256": digest(assimilation)},
        "residuals": {"path": "evaluation.csv", "sha256": digest(residuals)},
        "history": observations[:1],
        "heldout": observations[1:],
        "error_assumption": "independent_gaussian_sd",
        "percentile_convention": {"latent_truth": "linear", "noisy_observation": "inverse_cdf"},
    }
    return {
        "folder": folder,
        "manifest": manifest,
        "rows": rows,
        "split": folder / "split.json",
        "assimilation": assimilation,
        "residuals": residuals,
        "out": tmp_path / "report",
    }


def save_bundle(bundle, rehash=True):
    write_csv(bundle["residuals"], bundle["rows"])
    if rehash:
        for field in ("assimilation", "residuals"):
            bundle["manifest"][field]["sha256"] = digest(bundle[field])
    bundle["split"].write_text(json.dumps(bundle["manifest"], allow_nan=True))


def run_cli(bundle, script=SCRIPT):
    save_bundle(bundle)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return subprocess.run(
        [sys.executable, str(script), "--split", str(bundle["split"]), "--out", str(bundle["out"])],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_latent_empirical_crps_and_band():
    scorer = load_scorer()
    result = scorer.score_distribution([0, 2], 1, 100, "latent_truth")
    assert result == pytest.approx({"bias": 0, "coverage90": 1, "width90": 1.8, "crps": 0.5})


def test_noisy_centered_normal_closed_form():
    scorer = load_scorer()
    result = scorer.score_distribution([1, 1], 1, 2, "noisy_observation")
    assert result["crps"] == pytest.approx(2 * (math.sqrt(2) - 1) / math.sqrt(math.pi))
    assert result["width90"] == pytest.approx(4 * 1.6448536269514722)
    assert result["coverage90"] == 1


@pytest.mark.parametrize("kind", ["latent_truth", "noisy_observation"])
def test_ensemble_of_identical_members_has_exactly_zero_bias(kind):
    scorer = load_scorer()
    result = scorer.score_distribution([220.0] * 50, 220.0, 1.0, kind)
    assert result["bias"] == 0


@pytest.mark.parametrize("kind", ["latent_truth", "noisy_observation"])
def test_scores_are_permutation_invariant(kind):
    scorer = load_scorer()
    assert scorer.score_distribution([4, -2, 1], 0, 0.5, kind) == pytest.approx(
        scorer.score_distribution([1, 4, -2], 0, 0.5, kind)
    )


def test_cli_reports_native_groups_without_acceptance(bundle):
    result = run_cli(bundle)
    assert result.returncode == 0, result.stderr
    report = json.loads((bundle["out"] / "heldout.json").read_text())
    assert report["schema"] == "ahm-heldout-report/1"
    assert "retrospective split validation" in report["scope"]
    assert "execution-time independence" in report["limits"]
    assert "verdict" not in report
    assert len(report["scores"]) == 4
    for row in report["scores"]:
        assert row["UNIT"] == "SM3/DAY"
        assert row["bias"] == row["rms"] == row["normalized_rms"] == 0
        assert row["crps"] == 0.5
        assert row["coverage90"] == 1
        assert row["width90"] == pytest.approx(1.8)


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("missing_member", "member set"),
        ("extra_ensemble", "ensemble set"),
        ("duplicate_pair", "duplicate REAL/OBS"),
        ("missing_obs", "observations"),
        ("bad_unit", "UNIT"),
        ("bad_key", "KEY"),
        ("bad_date", "DATE"),
        ("bad_observed", "OBSERVED"),
        ("bad_error", "ERROR"),
        ("nan", "finite"),
        ("infinity", "finite"),
        ("zero_error", "positive"),
        ("fractional_real", "integer"),
        ("negative_real", "integer"),
        ("duplicate_ids", "members"),
        ("bool_id", "members"),
        ("single_id", "members"),
        ("unknown_field", "fields"),
        ("unknown_reference_field", "fields"),
        ("bad_hash", "sha256"),
        ("bad_kind", "kind"),
        ("bad_convention", "percentile"),
        ("bad_assumption", "error_assumption"),
        ("empty_history", "history"),
        ("empty_heldout", "heldout"),
        ("duplicate_physical", "KEY/DATE"),
        ("duplicate_obs", "OBS"),
        ("temporal_leak", "history_end"),
        ("history_after_cutoff", "history_end"),
        ("bad_manifest_date", "ISO date"),
        ("blank_unit", "UNIT"),
        ("manifest_nonfinite", "finite"),
        ("unsupported_include", "INCLUDE"),
        ("unsupported_type", "unsupported"),
        ("renamed_leak", "heldout"),
        ("missing_assimilation", "assimilation"),
        ("extra_assimilation", "assimilation"),
        ("unparsed_text", "unsupported"),
        ("duplicate_obs_field", "duplicate"),
        ("history_value_mismatch", "actual assimilation VALUE"),
        ("history_error_mismatch", "actual assimilation ERROR"),
    ],
)
def test_refuses_invalid_contracts_without_writing(bundle, mutation, message):
    m, rows = bundle["manifest"], bundle["rows"]
    if mutation == "missing_member":
        bundle["rows"] = [r for r in rows if not (r["ENSEMBLE"] == "posterior" and r["REAL"] == 2)]
    elif mutation == "extra_ensemble":
        rows.append({**rows[0], "ENSEMBLE": "extra"})
    elif mutation == "duplicate_pair":
        rows.append(rows[0].copy())
    elif mutation == "missing_obs":
        bundle["rows"] = [r for r in rows if r["OBS"] != "f"]
    elif mutation.startswith("bad_") and mutation[4:].upper() in {"UNIT", "KEY", "DATE", "OBSERVED", "ERROR"}:
        rows[0][mutation[4:].upper()] = "wrong" if mutation not in ("bad_observed", "bad_error") else 123
    elif mutation in ("nan", "infinity"):
        rows[0]["SIMULATED"] = float(mutation if mutation == "nan" else "inf")
    elif mutation == "zero_error":
        m["heldout"][0]["ERROR"] = 0
    elif mutation in ("fractional_real", "negative_real"):
        rows[0]["REAL"] = 0.5 if mutation == "fractional_real" else -1
    elif mutation in ("duplicate_ids", "bool_id", "single_id"):
        m["ensembles"]["prior"] = {"duplicate_ids": [0, 0], "bool_id": [False, 2], "single_id": [0]}[mutation]
    elif mutation == "unknown_field":
        m["ignored"] = 1
    elif mutation == "unknown_reference_field":
        m["heldout"][0]["ignored"] = float("nan")
    elif mutation == "bad_hash":
        m["residuals"]["sha256"] = "not-a-hash"
        save_bundle(bundle, rehash=False)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--split", str(bundle["split"]), "--out", str(bundle["out"])],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1 and message in result.stderr, result.stderr
        assert not bundle["out"].exists()
        return
    elif mutation == "bad_kind":
        m["heldout"][0]["kind"] = "guess"
    elif mutation == "bad_convention":
        m["percentile_convention"]["latent_truth"] = "nearest"
    elif mutation == "bad_assumption":
        m["error_assumption"] = "anything"
    elif mutation in ("empty_history", "empty_heldout"):
        m[mutation[6:]] = []
    elif mutation == "duplicate_physical":
        m["heldout"][0].update(KEY="FOPR", DATE="2020-01-01")
    elif mutation == "duplicate_obs":
        m["heldout"][0]["OBS"] = "h"
    elif mutation == "temporal_leak":
        m["history_end"] = "2020-03-01"
    elif mutation == "history_after_cutoff":
        m["history_end"] = "2019-12-01"
    elif mutation == "bad_manifest_date":
        m["heldout"][0]["DATE"] = "20200301"
    elif mutation == "blank_unit":
        m["heldout"][0]["UNIT"] = " "
    elif mutation == "manifest_nonfinite":
        m["heldout"][0]["OBSERVED"] = float("inf")
    elif mutation == "unsupported_include":
        with bundle["assimilation"].open("a") as stream:
            stream.write('INCLUDE "unbound.obs"\n')
    elif mutation == "unsupported_type":
        with bundle["assimilation"].open("a") as stream:
            stream.write("GENERAL_OBSERVATION rft { DATA = RFT; VALUE = 1; ERROR = 2; RESTART = 0; };\n")
    elif mutation == "renamed_leak":
        with bundle["assimilation"].open("a") as stream:
            stream.write(
                "SUMMARY_OBSERVATION renamed { KEY = FOPR; DATE = 2020-03-01; VALUE = 1; ERROR = 2; };\n"
            )
    elif mutation == "missing_assimilation":
        bundle["assimilation"].write_text("-- empty\n")
    elif mutation == "extra_assimilation":
        with bundle["assimilation"].open("a") as stream:
            stream.write(
                "SUMMARY_OBSERVATION extra { KEY = FWPR; DATE = 2020-01-01; VALUE = 1; ERROR = 2; };\n"
            )
    elif mutation == "unparsed_text":
        with bundle["assimilation"].open("a") as stream:
            stream.write("SOMETHING_STRANGE\n")
    elif mutation == "duplicate_obs_field":
        bundle["assimilation"].write_text(
            bundle["assimilation"].read_text().replace("KEY = FOPR;", "KEY = FWPR; KEY = FOPR;")
        )
    elif mutation in ("history_value_mismatch", "history_error_mismatch"):
        field = "OBSERVED" if mutation == "history_value_mismatch" else "ERROR"
        m["history"][0][field] = 5.0
        for row in rows:
            if row["OBS"] == "h":
                row[field] = 5.0
    else:
        raise AssertionError(mutation)
    result = run_cli(bundle)
    assert result.returncode == 1 and message in result.stderr, result.stderr
    assert not bundle["out"].exists()


def test_full_precision_reference_values_survive_the_csv_round_trip(bundle):
    values = {"OBSERVED": 491.66912841796875, "ERROR": 25.669802246093752}
    bundle["manifest"]["heldout"][0].update(values)
    for row in bundle["rows"]:
        if row["OBS"] == "f":
            row.update(values)
    result = run_cli(bundle)
    assert result.returncode == 0, result.stderr


def test_relative_assimilation_error_is_the_effective_ert_error(bundle):
    bundle["assimilation"].write_text(
        "SUMMARY_OBSERVATION h { KEY = FOPR; DATE = 2020-01-01; VALUE = 1; ERROR = 2; ERROR_MODE = REL; };\n"
    )
    assert run_cli(bundle).returncode == 0
    bundle["out"] = bundle["out"].parent / "report-wrong-relative-error"
    bundle["assimilation"].write_text(
        "SUMMARY_OBSERVATION h { KEY = FOPR; DATE = 2020-01-01; VALUE = 1; ERROR = 1; ERROR_MODE = REL; };\n"
    )
    result = run_cli(bundle)
    assert result.returncode == 1 and "actual assimilation ERROR" in result.stderr, result.stderr


def brute_force_scores(samples, reference, error, kind):
    samples = np.asarray(samples, dtype=float)
    if kind == "latent_truth":
        edges = np.concatenate(
            (
                [min(samples.min(), reference) - 1],
                np.sort(np.append(samples, reference)),
                [max(samples.max(), reference) + 1],
            )
        )
        crps = sum(
            ((samples <= a).mean() - float(a >= reference)) ** 2 * (b - a)
            for a, b in zip(edges[:-1], edges[1:], strict=True)
        )
        lo, hi = np.percentile(samples, [5, 95])
        return crps, lo, hi
    grid = np.union1d(
        np.linspace(samples.min() - 12 * error, samples.max() + 12 * error, 400_001), [reference]
    )
    cdf = np.mean(
        [0.5 * np.vectorize(math.erfc)(-(grid - v) / (error * math.sqrt(2))) for v in samples], axis=0
    )

    def trapezoid(values, x):
        return float(np.sum((values[1:] + values[:-1]) / 2 * np.diff(x)))

    below, above = grid <= reference, grid >= reference
    crps = trapezoid(cdf[below] ** 2, grid[below]) + trapezoid((1 - cdf[above]) ** 2, grid[above])
    lo, hi = np.interp([0.05, 0.95], cdf, grid)
    return crps, lo, hi


@pytest.mark.parametrize("kind", ["latent_truth", "noisy_observation"])
@pytest.mark.parametrize("reference", [0.9, 7.0])
def test_scores_match_brute_force_numeric_integration(kind, reference):
    scorer = load_scorer()
    samples, error = [-1.3, 0.4, 2.2, 5.0, 2.2], 0.7
    crps, lo, hi = brute_force_scores(samples, reference, error, kind)
    result = scorer.score_distribution(samples, reference, error, kind)
    assert result["crps"] == pytest.approx(crps, abs=2e-6)
    assert result["width90"] == pytest.approx(hi - lo, abs=1e-4)
    assert result["coverage90"] == float(lo <= reference <= hi)
    assert result["bias"] == pytest.approx(np.mean(samples) - reference)


def test_each_key_is_scored_in_its_own_native_unit_without_pooling(bundle):
    bundle["assimilation"].write_text(
        bundle["assimilation"].read_text()
        + "SUMMARY_OBSERVATION hw { KEY = FWCT; DATE = 2020-01-01; VALUE = 0.2; ERROR = 0.05; };\n"
    )
    extra = [
        {
            "OBS": "hw",
            "KEY": "FWCT",
            "DATE": "2020-01-01",
            "UNIT": "fraction",
            "OBSERVED": 0.2,
            "ERROR": 0.05,
            "kind": "noisy_observation",
        },
        {
            "OBS": "fw",
            "KEY": "FWCT",
            "DATE": "2020-03-01",
            "UNIT": "fraction",
            "OBSERVED": 0.3,
            "ERROR": 0.05,
            "kind": "latent_truth",
        },
    ]
    bundle["manifest"]["history"].append(extra[0])
    bundle["manifest"]["heldout"].append(extra[1])
    for ensemble in ("prior", "posterior"):
        for member, simulated in ((0, 0.4), (2, 0.6)):
            for obs in extra:
                bundle["rows"].append(
                    {
                        "ENSEMBLE": ensemble,
                        "REAL": member,
                        "SIMULATED": simulated,
                        **{key: obs[key] for key in COLUMNS if key in obs},
                    }
                )
    result = run_cli(bundle)
    assert result.returncode == 0, result.stderr
    scores = json.loads((bundle["out"] / "heldout.json").read_text())["scores"]
    assert len(scores) == 8
    by_key = {(row["ensemble"], row["split"], row["KEY"]): row for row in scores}
    assert {row["UNIT"] for row in scores if row["KEY"] == "FOPR"} == {"SM3/DAY"}
    assert {row["UNIT"] for row in scores if row["KEY"] == "FWCT"} == {"fraction"}
    held = by_key[("posterior", "heldout", "FWCT")]
    assert held["bias"] == pytest.approx(0.2) and held["rms"] == pytest.approx(0.2)
    assert held["normalized_rms"] == pytest.approx(4.0)
    assert held["crps"] == pytest.approx(0.15) and held["coverage90"] == 0
    assert held["width90"] == pytest.approx(0.18)
    assert by_key[("posterior", "heldout", "FOPR")]["bias"] == 0


def test_unit_must_be_consistent_for_each_key_across_splits(bundle):
    bundle["manifest"]["heldout"][0]["UNIT"] = "STB/DAY"
    result = run_cli(bundle)
    assert result.returncode == 1 and "UNIT must be consistent" in result.stderr, result.stderr
    assert not bundle["out"].exists()


@pytest.mark.parametrize("field", ["assimilation", "residuals"])
def test_stale_hash_is_refused(bundle, field):
    save_bundle(bundle)
    with bundle[field].open("a") as stream:
        stream.write("\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--split", str(bundle["split"]), "--out", str(bundle["out"])],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1 and "sha256 mismatch" in result.stderr, result.stderr
    assert not bundle["out"].exists()


@pytest.mark.parametrize("location", ["existing", "inside", "ancestor"])
def test_output_must_be_fresh_and_disjoint(bundle, location):
    if location == "existing":
        bundle["out"].mkdir()
    elif location == "inside":
        bundle["out"] = bundle["folder"] / "new-output"
    else:
        bundle["out"] = bundle["folder"].parent
    result = run_cli(bundle)
    assert result.returncode == 1 and ("fresh" in result.stderr or "input" in result.stderr), result.stderr
    assert not (bundle["out"] / "heldout.json").exists()


def test_installed_scripts_layout_and_help_without_site_packages(bundle, tmp_path):
    target = tmp_path / "installed/history-match-qc/scripts"
    target.mkdir(parents=True)
    for name in ("heldout_score.py", "coverage.py", "obs_qc.py"):
        assert (SCRIPT.parent / name).is_file(), f"missing {name}"
        shutil.copy2(SCRIPT.parent / name, target / name)
    help_run = subprocess.run(
        [sys.executable, "-S", str(target / "heldout_score.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_run.returncode == 0, help_run.stderr
    result = run_cli(bundle, target / "heldout_score.py")
    assert result.returncode == 0, result.stderr
