"""qa-qc-preread + gate-packet on synthetic deliverables.

Stages come only from contract gate_stages (no built-in names). Draft builds with open issues;
strict refuses. Numbers in the pre-read are quoted from the bands CSV.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, project_block, run, write_contracts, write_profiles

PREREAD = SKILLS / "qa-qc-preread/scripts/qa_qc_preread.py"
GATE = SKILLS / "gate-packet/scripts/gate_packet.py"
BANDS = SKILLS / "ensemble-bands/scripts/ensemble_bands.py"


def _deliverables(tmp_path: Path, *, fail_scenario: bool = False, **over):
    need("yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, **over))
    deliv = tmp_path / "deliverables"
    write_profiles(deliv / "profiles", n=5)
    (deliv / "profiles/manifest.json").write_text(
        json.dumps({"skill": "ensemble-profiles", "n_realizations": 5}), encoding="utf-8"
    )
    proc = run(BANDS, "--contract", proj, env=env)
    assert proc.returncode == 0, proc.stderr
    if fail_scenario:
        (deliv / "scenario_qc").mkdir()
        matrix = {
            "any_fail": True,
            "n_scenarios": 1,
            "headline_vector": "FOPT",
            "rows": [
                {
                    "scenario_id": "cut_p2",
                    "status": "FAIL",
                    "flags": "unexpected_jump",
                    "headline_rel_delta": 0.2,
                }
            ],
        }
        (deliv / "scenario_qc/scenario_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
    return proj, env


def test_preread_default_stage_quotes_bands(tmp_path: Path):
    proj, env = _deliverables(tmp_path)
    out = tmp_path / "pre"
    proc = run(PREREAD, "--contract", proj, "--outdir", out, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    outline = json.loads((out / "preread_outline.json").read_text())
    assert outline["stage"] == "VPBO"  # gate_default_stage
    assert outline["p10_p90"] == "numerical"
    keys = [h["KEY"] for h in outline["band_highlights"]]
    assert keys[:2] == ["FOPT", "FGPT"] and "WOPR:P1" in keys  # vectors.headline + wells.plot_default
    fopt = next(h for h in outline["band_highlights"] if h["KEY"] == "FOPT")
    assert float(fopt["P10"]) == pytest.approx(113984.0)  # quoted from bands CSV, not invented
    assert outline["open_issues"] == []
    md = (out / "preread.md").read_text()
    assert "## Open issues" in md and "numerical" in md
    assert (out / "preread.html").is_file()


def test_preread_lists_scenario_fail(tmp_path: Path):
    proj, env = _deliverables(tmp_path, fail_scenario=True)
    out = tmp_path / "pre"
    proc = run(PREREAD, "--contract", proj, "--stage", "BPBO", "--outdir", out, env=env)
    assert proc.returncode == 0, proc.stderr
    ids = {i["id"] for i in json.loads((out / "preread_outline.json").read_text())["open_issues"]}
    assert "scenario_qc_fail:cut_p2" in ids


def test_unknown_stage_exits_2(tmp_path: Path):
    proj, env = _deliverables(tmp_path)
    for script in (PREREAD, GATE):
        proc = run(script, "--contract", proj, "--stage", "lab-DG0", "--outdir", tmp_path / "x", env=env)
        assert proc.returncode == 2, proc.stdout
        assert "not in contract gate_stages" in proc.stderr


def test_gate_draft_with_open_issues_exit_0(tmp_path: Path):
    proj, env = _deliverables(tmp_path, fail_scenario=True)
    out = tmp_path / "g"
    proc = run(GATE, "--contract", proj, "--stage", "VPBO", "--outdir", out, "--allow-open-issues", env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    packet = json.loads((out / "gate_packet.json").read_text())
    assert packet["status"] in {"DRAFT", "PASS"} and packet["mode"] == "draft"
    assert packet["p10_p90"] == "numerical" and packet["open_issues_count"] >= 1
    assert "## Open issues" in (out / "preread.md").read_text()


def test_gate_strict_stage_default_refuses(tmp_path: Path):
    proj, env = _deliverables(tmp_path, fail_scenario=True)
    out = tmp_path / "g"
    proc = run(GATE, "--contract", proj, "--stage", "DG0", "--outdir", out, env=env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    packet = json.loads((out / "gate_packet.json").read_text())
    assert packet["status"] == "REFUSED" and packet["mode"] == "strict"
    assert packet["refuse_reasons"]
    assert (out / "preread.md").is_file()  # humans still see the open issues


def test_gate_clean_strict_passes(tmp_path: Path):
    proj, env = _deliverables(tmp_path)
    out = tmp_path / "g"
    proc = run(GATE, "--contract", proj, "--stage", "DG0", "--outdir", out, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((out / "gate_packet.json").read_text())["status"] == "PASS"


def test_gate_missing_artifact_refuses(tmp_path: Path):
    stages = {
        "VPBO": {
            "mode_default": "strict",
            "required_artifacts": ["profiles", "bands", "scenario_qc", "plots"],
        }
    }
    proj, env = _deliverables(tmp_path, gate_stages=stages)
    out = tmp_path / "g"
    proc = run(GATE, "--contract", proj, "--strict", "--outdir", out, env=env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    packet = json.loads((out / "gate_packet.json").read_text())
    assert packet["status"] == "REFUSED"
    assert {"scenario_qc", "plots"} <= set(packet["missing_required_artifacts"])


def test_gate_bad_mode_default_exits_2(tmp_path: Path):
    stages = {"VPBO": {"mode_default": "maybe", "required_artifacts": ["profiles"]}}
    proj, env = _deliverables(tmp_path, gate_stages=stages)
    proc = run(GATE, "--contract", proj, "--outdir", tmp_path / "g", env=env)
    assert proc.returncode == 2 and "mode_default" in proc.stderr
