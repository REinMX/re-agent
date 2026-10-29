"""Evidence-bound screening and semantic prior compatibility, without simulation."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")

import test_screening_integrity as integrity  # noqa: E402
from test_screening_integrity import (  # noqa: E402
    assert_rejected,
    cli,
    run_preflight,
    run_screen,
    screening_record,
)

case = integrity.case


def refused(result, pattern="screen|evidence|prior|observation"):
    import re

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is False and report["command"] is None
    assert any(i["severity"] == "error" and re.search(pattern, i["message"], re.I) for i in report["items"])
    return report


def test_record_binds_the_full_domain_and_source_bytes(case):
    record = screening_record(case)
    assert record["schema"] == "ahm-screening/2"
    assert record["priors"] == {"A": {"distribution": "UNIFORM", "arguments": {"min": 0.0, "max": 1.0}}}
    assert record["domain"] == {
        "ensemble": "screen",
        "members": [0, 1],
        "observations": ["O1", "O2"],
        "observations_authoritative": True,
    }
    assert record["observation_metadata"]["O1"] == case.observations[0]


def test_evidence_paths_are_relative_and_hashes_match(case):
    record = screening_record(case)
    assert set(record["evidence"]) == {"design", "residuals", "observations"}
    for name, entry in record["evidence"].items():
        path = Path(entry["path"])
        assert not path.is_absolute(), name
        source = case.root / "screening" / path
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"]


def test_missing_inventory_produces_diagnostics_but_never_readiness(case):
    result = run_screen(case, observations=False)
    assert result.returncode == 0, result.stderr
    record = json.loads((case.root / "screening/screening.json").read_text())
    assert record["domain"]["observations_authoritative"] is False
    assert "diagnostic" in (result.stdout + result.stderr).lower()
    refused(run_preflight(case), "observation|inventory")


def test_missing_whole_observation_is_caught_by_independent_inventory(case):
    case.rows = [row for row in case.rows if row["OBS"] != "O2"]
    assert_rejected(case, run_screen(case), "observation|missing|coverage")


@pytest.mark.parametrize(
    "column,value", [("OBSERVED", 9.0), ("ERROR", 9.0), ("KEY", "OTHER"), ("DATE", "2002-01-01")]
)
def test_inventory_metadata_must_match_residuals(case, column, value):
    case.observations[0][column] = value
    assert_rejected(case, run_screen(case), f"{column}|metadata")


@pytest.mark.parametrize("source", ["design.csv", "residuals.csv", "observations.csv"])
@pytest.mark.parametrize("action", ["modify", "remove"])
def test_preflight_refuses_changed_or_missing_source(case, source, action):
    screening_record(case)
    path = case.root / source
    if action == "modify":
        path.write_text(path.read_text() + "\n")
    else:
        path.rename(case.root / (source + ".moved"))
    refused(run_preflight(case), "evidence|source|hash")


def test_complete_bundle_can_be_relocated_and_old_prior_need_not_exist(case, tmp_path):
    screening_record(case)
    relocated = tmp_path / "relocated"
    shutil.copytree(case.root / "screening", relocated / "screening")
    for name in ("design.csv", "residuals.csv", "observations.csv", "case.ert", "prior.dist"):
        shutil.copy2(case.root / name, relocated / name)
    case.prior.rename(case.root / "old-prior.dist")
    result = cli(
        "ahm_preflight.py",
        relocated / "case.ert",
        "--screening",
        relocated / "screening/screening.json",
        "--json",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["ready"]


@pytest.mark.parametrize("prior", ["A NORMAL 0.5 0.1\n", "A UNIFORM -1 1\n", "A UNIFORM 0 2\n"])
def test_material_prior_changes_require_rescreening(case, prior):
    screening_record(case)
    case.prior.write_text(prior)
    refused(run_preflight(case), "prior|distribution")


def test_comments_numeric_spelling_order_and_split_files_do_not_change_identity(case):
    case.prior.write_text("A UNIFORM 0 1\nB CONST 4\n")
    screening_record(case)
    case.prior.write_text("# moved the constant\nB CONST 4.0\n")
    (case.root / "other.dist").write_text("A uniform 0e0 1.000 # same prior\n")
    case.config.write_text("NUM_REALIZATIONS 100\nGEN_KW G prior.dist\nGEN_KW H other.dist\n")
    result = run_preflight(case)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["ready"]


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "null",
        "{}",
        '{"schema":"ahm-screening/1"}',
        '{"schema":"ahm-screening/2","parameters":null}',
        '{"schema":',
    ],
)
def test_malformed_and_legacy_records_refuse_cleanly_with_rescreen_action(case, payload):
    folder = case.root / "screening"
    folder.mkdir()
    (folder / "screening.json").write_text(payload)
    report = refused(run_preflight(case))
    assert any(
        "screen_params.py" in item["message"] for item in report["items"] if item["severity"] == "error"
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing_domain", "bad_keep", "duplicate_parameter", "bad_hash", "nonfinite", "missing_priors"],
)
def test_malformed_schema2_fields_refuse_cleanly(case, mutation):
    record = screening_record(case)
    if mutation == "missing_domain":
        record.pop("domain")
    elif mutation == "bad_keep":
        record["parameters"][0]["keep"] = "yes"
    elif mutation == "duplicate_parameter":
        record["parameters"].append(record["parameters"][0])
    elif mutation == "bad_hash":
        record["evidence"]["design"]["sha256"] = "not-a-hash"
    elif mutation == "nonfinite":
        record["parameters"][0]["chi2_swing"] = float("nan")
    else:
        record.pop("priors")
    (case.root / "screening/screening.json").write_text(json.dumps(record))
    refused(run_preflight(case))
