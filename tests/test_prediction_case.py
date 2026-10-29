"""prediction-case: contract-driven case folders with nreal/seed markers; never launches ERT."""

from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, run, write_contracts, write_ert_template, write_priors

SCRIPT = SKILLS / "prediction-case/scripts/prediction_case.py"


def _mod():
    need("numpy", "yaml")
    spec = importlib.util.spec_from_file_location("prediction_case", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lab(tmp_path: Path):
    need("numpy", "yaml")
    proj, env = write_contracts(tmp_path)
    write_priors(tmp_path)
    write_ert_template(tmp_path)
    return proj, env


def test_rebase_relative_paths():
    mod = _mod()
    text = (
        "GEN_KW ROCK ../input/rock.tmpl out.inc ../input/priors.txt\n"
        "DEFINE <SCRATCH> <CONFIG_PATH>/../../../scratch\n"
        "FORWARD_MODEL COPY_FILE(<FROM>=<CONFIG_PATH>/../../eclipse/model/X.DATA, <TO>=x)\n"
        "RUNPATH realization-<IENS>/iter-<ITER>\n"
    )
    out = mod.rebase_relative_paths(text, "/p/ert/model", "/p/ert/cases/c1")
    assert "../../input/rock.tmpl" in out and "../../input/priors.txt" in out
    assert "<CONFIG_PATH>/../../../../scratch" in out
    assert "<CONFIG_PATH>/../../../eclipse/model/X.DATA" in out
    assert "realization-<IENS>/iter-<ITER>" in out  # untouched
    # same folder: no change
    assert mod.rebase_relative_paths(text, "/p/ert/model", "/p/ert/model") == text


def test_render_ert_markers():
    mod = _mod()
    tpl = "NUM_REALIZATIONS 10\nGEN_KW A ../input/a.txt\n"
    out = mod.render_ert(tpl, name="c1", nreal=20, seed=7, case_dir=None)
    assert re.search(r"^\s*NUM_REALIZATIONS\s+20\b", out, re.M)
    assert re.search(r"^\s*RANDOM_SEED\s+7\b", out, re.M)
    assert "DEFINE  <CASE_DIR>      c1" in out
    assert "../../input/a.txt" in out  # default layout ert/model -> ert/cases/<name>


def test_case_from_contract(lab, tmp_path: Path):
    proj, env = lab
    result = run(SCRIPT, "--contract", proj, "--name", "pred_a", "--nreal", "12", "--seed", "5", env=env)
    assert result.returncode == 0, result.stderr + result.stdout
    case = tmp_path / "project/ert/cases/pred_a"
    text = (case / "pred_a.ert").read_text(encoding="utf-8")
    assert re.search(r"^\s*NUM_REALIZATIONS\s+12\b", text, re.M)
    assert re.search(r"^\s*RANDOM_SEED\s+5\b", text, re.M)
    assert "<CASE_DIR>      pred_a" in text
    # paths rebased from ert/model to ert/cases/pred_a and still point at real files
    assert "../../input/templates/rock.tmpl" in text
    assert (case / "../../input/templates/rock.tmpl").resolve().is_file()
    with (case / "design_matrix.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 12 and {"PORO_MULT", "PERM_MULT", "SWL"} <= set(rows[0])
    meta = json.loads((case / "case_meta.json").read_text(encoding="utf-8"))
    assert meta["nreal"] == 12 and meta["seed"] == 5 and meta["launches"] is False
    assert (case / "priors_snapshot/rock_priors.txt").is_file()  # uncertainty.prior_files
    readme = (case / "README.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in readme  # no machine paths: relative cd + profile activate
    assert "true" in readme  # profile activate line


def test_groups_subset(lab, tmp_path: Path):
    proj, env = lab
    result = run(
        SCRIPT,
        "--contract",
        proj,
        "--name",
        "rock",
        "--nreal",
        "4",
        "--seed",
        "1",
        "--groups",
        "ROCK",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    with (tmp_path / "project/ert/cases/rock/design_matrix.csv").open(newline="") as fh:
        fields = csv.DictReader(fh).fieldnames or []
    assert "PORO_MULT" in fields and "SWL" not in fields


def test_cli_overrides_cases_root(lab, tmp_path: Path):
    proj, env = lab
    out_root = tmp_path / "elsewhere"
    result = run(
        SCRIPT,
        "--contract",
        proj,
        "--name",
        "c4",
        "--nreal",
        "3",
        "--seed",
        "9",
        "--cases-root",
        out_root,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    text = (out_root / "c4/c4.ert").read_text(encoding="utf-8")
    # rebased to the new folder, files still reachable
    tmpl = re.search(r"GEN_KW\s+ROCK\s+(\S+)", text).group(1)
    assert (out_root / "c4" / tmpl).resolve().is_file()


def test_incomplete_prior_refuses_invent(lab, tmp_path: Path):
    proj, env = lab
    bad = tmp_path / "bad.csv"
    bad.write_text("group,name,distribution,arg1,arg2,notes\nROCK,PORO_MULT,UNIFORM,,,\n", encoding="utf-8")
    result = run(
        SCRIPT,
        "--contract",
        proj,
        "--name",
        "should_fail",
        "--nreal",
        "2",
        "--seed",
        "1",
        "--parameters-csv",
        bad,
        env=env,
    )
    assert result.returncode == 2
    assert "incomplete prior" in result.stderr
    assert not (tmp_path / "project/ert/cases/should_fail").exists()


def test_missing_template_in_contract_exits_2(tmp_path: Path):
    need("numpy", "yaml")
    from synth_fmu import project_block

    proj_data = project_block(tmp_path)
    proj_data["prediction_case"] = {"cases_root": "project/ert/cases"}
    proj, env = write_contracts(tmp_path, project=proj_data)
    write_priors(tmp_path)
    result = run(SCRIPT, "--contract", proj, "--name", "x", "--nreal", "2", "--seed", "1", env=env)
    assert result.returncode == 2
    assert "template_ert" in result.stderr
