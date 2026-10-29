"""stage-contract: accepts a good v2 / v1 contract, refuses a broken one (exit 2)."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, project_block, run, write_contracts

SCRIPT = SKILLS / "stage-contract/scripts/stage_contract.py"


@pytest.fixture(autouse=True)
def _yaml():
    need("yaml")


def test_v2_contract_ok(tmp_path: Path):
    proj, env = write_contracts(tmp_path)
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 0, result.stderr
    assert "profile: home (schema 2)" in result.stdout
    assert "p10_p90: numerical" in result.stdout
    assert "eclbase: eclipse/model/TOY" in result.stdout
    assert "gate_stages: BPBO, VPBO, DG0" in result.stdout


def test_v2_contract_json_and_profile_flag(tmp_path: Path):
    proj, env = write_contracts(tmp_path)
    result = run(SCRIPT, "--contract", proj, "--profile", "home", "--json", env=env)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["p10_p90"] == "numerical"
    assert data["scenario_qc"]["fail_on_missing_vector"] is True
    bad = run(SCRIPT, "--contract", proj, "--profile", "nope", env=env)
    assert bad.returncode == 2 and "unknown profile" in bad.stderr


def test_open_simulator_name_is_accepted(tmp_path: Path):
    proj, env = write_contracts(tmp_path)
    user = Path(env["RE_AGENT_CONTRACT"])
    user.write_text(user.read_text().replace("name: FLOW", "name: OTHERSIM"), encoding="utf-8")
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 0, result.stderr
    assert "OTHERSIM" in result.stdout


def test_v1_flat_contract_ok(tmp_path: Path):
    flat = tmp_path / "site.contract.yml"
    flat.write_text(
        textwrap.dedent(
            """\
            simulator: FLOW
            p10_p90: numerical
            required_summary: [FOPT, FGPT, FPR]
            paths:
              eclbase: eclipse/model/TOY
              deliverables: deliverables
            approvals: [ert_run, everest_run, delete]
            everest: disabled
            sumo: disabled
            """
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("RE_AGENT_")}
    env.update(HOME=str(tmp_path / "nohome"), USERPROFILE=str(tmp_path / "nohome"))
    result = run(SCRIPT, flat, env=env)
    assert result.returncode == 0, result.stderr
    assert "profile: legacy (schema 1)" in result.stdout


def test_missing_eclbase_refused(tmp_path: Path):
    proj_data = project_block(tmp_path, eclbase=None)
    proj, env = write_contracts(tmp_path, project=proj_data)
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 2
    assert "eclbase" in result.stderr


def test_bad_p10_refused(tmp_path: Path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, p10_p90="maybe"))
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 2
    assert "p10_p90" in result.stderr


def test_placeholder_refused(tmp_path: Path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, field="<ASK: field name>"))
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 2
    assert "placeholder" in result.stderr.lower()


def test_legacy_approvals_key_is_ignored(tmp_path: Path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, approvals=["ert_run"]))
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 0, result.stderr
    assert "\napprovals:" not in result.stdout


def test_gate_default_stage_must_exist(tmp_path: Path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, gate_default_stage="DG9"))
    result = run(SCRIPT, "--contract", proj, env=env)
    assert result.returncode == 2
    assert "DG9" in result.stderr


def test_missing_file(tmp_path: Path):
    result = run(SCRIPT, tmp_path / "nope.yml")
    assert result.returncode == 2
    assert "not found" in result.stderr
