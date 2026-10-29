"""scenario-qc + scenario-matrix-qc + deck_fingerprint on synthetic decks and profiles.

Flags: fingerprint_mismatch, missing_vector, unexpected_jump, unexpected_silent; expected_changes
turn an intentional change into PASS (info expected_jump). Headline vector from the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, project_block, run, write_contracts, write_deck, write_profiles, yaml_dump

FP = SKILLS / "scenario-qc/scripts/deck_fingerprint.py"
QC = SKILLS / "scenario-qc/scripts/scenario_qc.py"
MATRIX = SKILLS / "scenario-matrix-qc/scripts/scenario_matrix_qc.py"


@pytest.fixture(autouse=True)
def _deps():
    need("yaml", "numpy", "pandas")


def _scenario(
    root: Path,
    sid: str,
    *,
    orat: float = 100.0,
    p1: float = 1.0,
    p2: float = 1.0,
    meta: dict | None = None,
    drop_fopt: bool = False,
) -> Path:
    scen = root / "scenarios" / sid
    deck = write_deck(scen / "eclipse/model/TOY.DATA", orat=orat)
    proc = run(FP, "--data", deck, "--out", scen / "deck_fingerprint.json")
    assert proc.returncode == 0, proc.stderr
    prof = scen / "deliverables/profiles"
    path = write_profiles(prof, n=5, p1_factor=p1, p2_factor=p2)
    if drop_fopt:
        lines = [ln for ln in path.read_text().splitlines() if ",FOPT," not in ln]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runmeta = {
        "scenario_id": sid,
        "parent_scenario_id": "baseline",
        "profiles_dir": str(prof),
        "p10_p90": "numerical",
        "changes": {"wells": [], "constraints": [], "schedule": []},
        "changed_keywords": [],
        "touched_wells": [],
        "demo": False,
    }
    runmeta.update(meta or {})
    (scen / "runmeta.yml").write_text(yaml_dump(runmeta), encoding="utf-8")
    return scen


CUT_META = {
    "changes": {"constraints": [{"keyword": "WCONPROD", "well": "P1"}], "wells": [], "schedule": []},
    "changed_keywords": ["WCONPROD"],
    "touched_wells": ["P1"],
    "expected_changes": {
        "wells": ["P1"],
        "keywords": ["WCONPROD"],
        "field_vectors_may_move": ["FOPT", "FOPR"],
        "max_rel_delta": "any",
    },
}


@pytest.fixture
def lab(tmp_path: Path):
    proj, env = write_contracts(tmp_path)
    _scenario(tmp_path, "baseline")
    return proj, env, tmp_path


def _qc(lab, sid: str):
    proj, env, root = lab
    proc = run(QC, "--contract", proj, "--scenario", sid, env=env)
    report_path = root / "scenarios" / sid / "deliverables/scenario_qc/scenario_qc_report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    return proc, report


def test_deck_fingerprint(tmp_path: Path):
    deck = write_deck(tmp_path / "TOY.DATA")
    out = tmp_path / "fp.json"
    proc = run(FP, "--data", deck, "--out", out)
    assert proc.returncode == 0, proc.stderr
    fp = json.loads(out.read_text())
    assert "WCONPROD" in fp["tracked_keywords_present"]
    assert {"P1", "P2"} <= set(fp["wells_mentioned"])
    assert len(fp["combined_sha256"]) == 64
    out2 = tmp_path / "fp2.json"
    run(FP, "--data", write_deck(tmp_path / "B.DATA", orat=50), "--out", out2)
    assert json.loads(out2.read_text())["combined_sha256"] != fp["combined_sha256"]


def test_deck_fingerprint_from_contract_runpath(tmp_path: Path):
    proj, env = write_contracts(tmp_path)
    runpath = tmp_path / "run"
    write_deck(runpath / "eclipse/model/TOY.DATA")
    out = tmp_path / "fp.json"
    proc = run(FP, "--contract", proj, "--runpath", runpath, "--out", out, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["combined_sha256"]


def test_baseline_vs_self_passes(lab):
    proc, report = _qc(lab, "baseline")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert report["status"] == "PASS" and report["flags"] == []
    assert report["p10_p90"] == "numerical"


def test_intentional_change_with_expected_changes_passes(lab):
    _scenario(lab[2], "cut_p1", orat=50, p1=0.5, meta=CUT_META)
    proc, report = _qc(lab, "cut_p1")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert report["status"] == "PASS"
    assert "expected_jump" in report.get("info_flags", [])
    assert "unexpected_jump" not in report["flags"]
    assert "P1" in report["checks"]["expected_wells"]


def test_jump_on_untouched_well_fails(lab):
    _scenario(lab[2], "cut_bad", orat=50, p1=0.5, p2=1.5, meta=CUT_META)
    proc, report = _qc(lab, "cut_bad")
    assert proc.returncode == 2, proc.stdout
    assert "unexpected_jump" in report["flags"]


def test_unexpected_jump_without_any_change(lab):
    _scenario(lab[2], "jump", p2=1.5)
    proc, report = _qc(lab, "jump")
    assert proc.returncode == 2
    assert "unexpected_jump" in report["flags"]


def test_unexpected_silent(lab):
    meta = dict(CUT_META)
    meta.pop("expected_changes")
    _scenario(lab[2], "silent", orat=50, meta=meta)
    proc, report = _qc(lab, "silent")
    assert proc.returncode == 2
    assert "unexpected_silent" in report["flags"]


def test_fingerprint_mismatch(lab):
    meta = dict(CUT_META)
    meta.pop("expected_changes")
    _scenario(lab[2], "claims", p1=0.5, meta=meta)  # claims WCONPROD, deck unchanged
    proc, report = _qc(lab, "claims")
    assert proc.returncode == 2
    assert "fingerprint_mismatch" in report["flags"]


def test_missing_vector_fails(lab):
    _scenario(lab[2], "nofopt", drop_fopt=True)
    proc, report = _qc(lab, "nofopt")
    assert proc.returncode == 2
    assert "missing_vector" in report["flags"]
    assert "FOPT" in report["checks"]["missing_vectors"]


def test_requires_scenario_qc_block(tmp_path: Path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, scenario_qc=None))
    _scenario(tmp_path, "baseline")
    proc = run(QC, "--contract", proj, "--scenario", "baseline", env=env)
    assert proc.returncode == 2 and "scenario_qc" in proc.stderr


def test_matrix_rollup_excludes_demo(lab):
    proj, env, root = lab
    _scenario(root, "cut_p1", orat=50, p1=0.5, meta=CUT_META)
    _scenario(root, "demo_jump", p2=1.5, meta={"demo": True})
    out = root / "deliverables/scenario_qc"
    proc = run(MATRIX, "--contract", proj, "--outdir", out, "--run-qc", "--include-baseline", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    matrix = json.loads((out / "scenario_matrix.json").read_text())
    assert matrix["headline_vector"] == "FOPT"
    ids = {r["scenario_id"]: r for r in matrix["rows"]}
    assert ids["cut_p1"]["status"] == "PASS"
    assert "excluded_from_gate_matrix" in ids["demo_jump"]["flags"]  # contract exclude_demo
    assert "headline_rel_delta" in ids["cut_p1"]
    assert (out / "scenario_matrix.csv").is_file() and (out / "scenario_matrix.md").is_file()
    # --include-demo overrides exclude_demo: the demo FAILs the matrix
    proc = run(MATRIX, "--contract", proj, "--outdir", out, "--run-qc", "--include-demo", env=env)
    assert proc.returncode == 2, proc.stdout
    ids = {r["scenario_id"]: r for r in json.loads((out / "scenario_matrix.json").read_text())["rows"]}
    assert ids["demo_jump"]["status"] == "FAIL"
